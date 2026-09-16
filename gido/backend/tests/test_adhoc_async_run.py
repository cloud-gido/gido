# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_adhoc_async.db")

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.workspace import AdhocRun, AdhocRunLogChunk, NodeInstance, TaskNode
from app.services import adhoc_run_worker as worker
from app.services.rbac_seed import migrate_adhoc_async_runs


def _session_factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_submit_node_run_is_single_flight_and_cancel_queued(monkeypatch):
    _engine, factory = _session_factory()
    db = factory()
    node = TaskNode(
        id=281,
        workspace_id=7,
        name="long_python",
        node_type="PYTHON",
        script_content="print('ok')",
        timeout_seconds=3600,
    )
    db.add(node)
    db.commit()

    first, reused = worker.submit_node_run(
        db,
        node=node,
        user_id=9,
        script_content=node.script_content,
        bizdate="2026-09-15",
    )
    assert reused is False
    assert first.status == "queued"
    assert first.node_instance_id

    second, reused = worker.submit_node_run(
        db,
        node=node,
        user_id=9,
        script_content=node.script_content,
        bizdate="2026-09-15",
    )
    assert reused is True
    assert second.id == first.id

    cancelled = worker.request_cancel(db, first)
    assert cancelled.status == "cancelled"
    assert cancelled.execution_key is None
    instance = db.query(NodeInstance).filter(NodeInstance.id == first.node_instance_id).one()
    assert instance.status == "cancelled"


def test_log_chunks_are_incremental(monkeypatch):
    _engine, factory = _session_factory()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    db = factory()
    row = AdhocRun(workspace_id=1, source="studio", status="running")
    db.add(row)
    db.commit()
    run_id = row.id
    db.close()

    worker.append_log(run_id, "one\n")
    worker.append_log(run_id, "two\n", "stderr")

    check = factory()
    chunks = (
        check.query(AdhocRunLogChunk)
        .filter(AdhocRunLogChunk.run_id == run_id)
        .order_by(AdhocRunLogChunk.seq)
        .all()
    )
    assert [(item.seq, item.stream, item.content) for item in chunks] == [
        (1, "stdout", "one\n"),
        (2, "stderr", "two\n"),
    ]


def test_stream_process_emits_before_completion(monkeypatch):
    emitted: list[str] = []
    monkeypatch.setattr(worker, "append_log", lambda _id, content, _stream="stdout": emitted.append(content))
    monkeypatch.setattr(worker, "_heartbeat", lambda _id: None)
    monkeypatch.setattr(worker, "_is_cancelled", lambda _id: False)

    lines = worker._stream_process(
        1,
        [
            sys.executable,
            "-c",
            "import time; print('first', flush=True); time.sleep(.7); print('second', flush=True)",
        ],
        timeout=5,
    )

    assert lines == ["first", "second"]
    assert emitted == ["first\n", "second\n"]


def test_log_redaction_masks_keys_and_injected_secrets():
    with worker._active_lock:
        worker._run_secrets[99] = ("workspace-secret-value",)
    try:
        redacted = worker._redact_log(
            99,
            "api_key=plain-value token: bearer-value workspace-secret-value",
        )
    finally:
        with worker._active_lock:
            worker._run_secrets.pop(99, None)

    assert redacted == "api_key=*** token: *** ***"


def test_stream_process_times_out(monkeypatch):
    monkeypatch.setattr(worker, "append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_heartbeat", lambda _id: None)
    monkeypatch.setattr(worker, "_is_cancelled", lambda _id: False)

    with pytest.raises(TimeoutError, match="运行超过 1 秒"):
        worker._stream_process(
            2,
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=1,
        )


def test_reclaim_stale_running_run(monkeypatch):
    _engine, factory = _session_factory()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    db = factory()
    row = AdhocRun(
        workspace_id=1,
        source="studio",
        status="running",
        heartbeat_at=datetime.utcnow() - timedelta(minutes=3),
        worker_id="dead-worker",
    )
    db.add(row)
    db.commit()
    run_id = row.id
    db.close()

    assert worker.reclaim_stale_runs() == 1
    check = factory()
    recovered = check.query(AdhocRun).filter(AdhocRun.id == run_id).one()
    assert recovered.status == "queued"
    assert recovered.worker_id is None


def test_migrate_adhoc_async_runs_upgrades_existing_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dw_adhoc_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    status VARCHAR(32)
                )
                """
            )
        )

    migrate_adhoc_async_runs(engine)
    migrate_adhoc_async_runs(engine)

    columns = {item["name"] for item in inspect(engine).get_columns("dw_adhoc_runs")}
    assert {"run_type", "execution_key", "heartbeat_at", "attempt_count"} <= columns
    assert inspect(engine).has_table("dw_adhoc_run_log_chunks")

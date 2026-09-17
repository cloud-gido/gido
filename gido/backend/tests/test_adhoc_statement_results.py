# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
from contextlib import contextmanager

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.workspace import AdhocRun, DataSource
from app.services import adhoc_run_store as store
from app.services import adhoc_run_worker as worker


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _running(db, token: str = "lease") -> AdhocRun:
    row = AdhocRun(workspace_id=1, source="studio", status="running", lease_token=token)
    db.add(row)
    db.commit()
    return row


def test_second_statement_failure_keeps_first_result_and_skips_rest():
    db = _db()
    run = _running(db)
    store.initialize_run_statements(db, run.id, ["select 1", "bad sql", "select 3"], "lease")

    store.start_run_statement(db, run.id, 0, "lease")
    store.append_statement_result_chunk(
        db, run.id, 0, [[1]], "lease", columns=["n"], column_types=["int"]
    )
    store.complete_run_statement_success(
        db, run.id, 0, "lease", columns=["n"], column_types=["int"]
    )
    db.refresh(run)
    assert run.status == "running"
    assert run.result_preview["rows"] == [[1]]

    store.start_run_statement(db, run.id, 1, "lease")
    store.complete_run_statement_failed(db, run.id, 1, "lease", "syntax error")
    store.complete_run_statement_skipped(db, run.id, 2, "lease", "前序语句失败")

    preview = store.build_result_preview_from_statements(db, run.id)
    assert preview["rows"] == [[1]]
    assert preview["partial_success"] is True
    legacy = store.truncate_result_preview(preview)
    assert legacy["rows"] == [[1]]
    assert legacy["partial_success"] is True
    assert legacy["has_errors"] is True
    assert [item.status for item in store.list_run_statements(db, run.id)] == [
        "success",
        "failed",
        "skipped",
    ]


def test_chunk_cursor_is_stable_and_lease_is_fenced():
    db = _db()
    run = _running(db)
    store.initialize_run_statements(db, run.id, ["select n"], "lease")
    store.start_run_statement(db, run.id, 0, "lease")
    for value in range(3):
        store.append_statement_result_chunk(
            db, run.id, 0, [[value]], "lease", columns=["n"]
        )

    first = store.paginate_statement_result_chunks(
        db, run.id, 0, after_cursor=-1, limit=2
    )
    second = store.paginate_statement_result_chunks(
        db, run.id, 0, after_cursor=first["next_cursor"], limit=2
    )
    assert [item.chunk_index for item in first["chunks"]] == [0, 1]
    assert [item.chunk_index for item in second["chunks"]] == [2]
    assert first["has_more"] is True
    assert second["has_more"] is False

    with pytest.raises(store.LeaseFenceError):
        store.append_statement_result_chunk(db, run.id, 0, [[99]], "stale")


def test_probe_failure_is_partial_and_continues(monkeypatch):
    from app.services import adhoc_sql_driver as driver

    db = _db()
    ds = DataSource(id=7, name="probe", ds_type="mysql")
    run = AdhocRun(
        workspace_id=1,
        source="probe",
        run_type="PROBE",
        datasource_id=7,
        sql_text="SELECT 1; SELECT bad; SELECT 3",
        request_payload={"limit": 10},
        status="running",
        lease_token="lease",
    )
    db.add_all([ds, run])
    db.commit()

    class Cursor:
        description = (("n", 3, None, None, None, None, None),)

        def __init__(self, index):
            self.index = index
            self.done = False

        def execute(self, _sql):
            if self.index == 1:
                raise RuntimeError("bad probe statement")

        def fetchmany(self, _size):
            if self.done:
                return []
            self.done = True
            return [(self.index + 1,)]

        def close(self):
            pass

    @contextmanager
    def opened(_ds, binding, *, timeout_seconds):
        conn = type(
            "Connection",
            (),
            {"cursor": lambda self: Cursor(binding.statement_index)},
        )()
        yield ("mysql", conn)

    monkeypatch.setattr(driver, "registered_connection", opened)
    monkeypatch.setattr(worker, "_is_cancelled", lambda *_args: False)
    monkeypatch.setattr(worker, "append_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.services.workspace_variables.substitute_script_variables",
        lambda *_args, **_kwargs: run.sql_text,
    )

    result = worker._run_probe(run, "lease", db)

    assert result["has_errors"] is True
    assert result["partial_success"] is True
    assert result["rows"] == [[3]]
    assert [item.status for item in store.list_run_statements(db, run.id)] == [
        "success",
        "failed",
        "success",
    ]

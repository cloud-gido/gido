# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.workspace import AdhocRun
from app.services import adhoc_run_worker as worker
from app.services import adhoc_sql_driver as driver


def _factory():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_lease_fences_heartbeat_and_log(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    db = factory()
    row = AdhocRun(
        workspace_id=1,
        source="probe",
        status="running",
        worker_id=worker._worker_name,
        lease_token="new-token",
        lease_expires_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()

    try:
        worker._heartbeat(row.id, "old-token")
        assert False, "stale lease must be rejected"
    except worker.LeaseLostError:
        pass
    worker.append_log(row.id, "stale\n", lease_token="old-token")
    worker.append_log(row.id, "owned\n", lease_token="new-token")

    db.expire_all()
    saved = db.query(AdhocRun).filter(AdhocRun.id == row.id).one()
    assert saved.log_next_seq == 2
    assert saved.log_bytes == len("owned\n".encode())


def test_reclaim_policy_does_not_retry_non_idempotent(monkeypatch):
    factory = _factory()
    monkeypatch.setattr(worker, "SessionLocal", factory)
    expired = datetime.utcnow() - timedelta(seconds=1)
    db = factory()
    rows = [
        AdhocRun(workspace_id=1, source="studio", run_type="SQL", status="running", lease_token="a", lease_expires_at=expired),
        AdhocRun(workspace_id=1, source="probe", run_type="PROBE", status="running", lease_token="b", lease_expires_at=expired),
        AdhocRun(workspace_id=1, source="studio", status="cancel_requested", lease_token="c", lease_expires_at=expired),
    ]
    db.add_all(rows)
    db.commit()

    assert worker.reclaim_stale_runs() == 3
    db.expire_all()
    assert [db.get(AdhocRun, row.id).status for row in rows] == [
        "failed",
        "queued",
        "cancelled",
    ]


def test_quota_filters_workspace_user_and_datasource(monkeypatch):
    factory = _factory()
    db = factory()
    db.add(AdhocRun(workspace_id=7, source="probe", triggered_by=9, datasource_id=11, status="running"))
    db.commit()
    candidate = AdhocRun(workspace_id=7, source="probe", triggered_by=10, datasource_id=12, status="queued")
    monkeypatch.setattr(worker.settings, "ADHOC_CONCURRENCY_GLOBAL", 0)
    monkeypatch.setattr(worker.settings, "ADHOC_CONCURRENCY_PER_WORKSPACE", 1)
    monkeypatch.setattr(worker.settings, "ADHOC_CONCURRENCY_PER_USER", 0)
    monkeypatch.setattr(worker.settings, "ADHOC_CONCURRENCY_PER_DATASOURCE", 0)
    assert worker._under_quota(db, candidate) is False


def test_postgres_cancel_discovers_only_worker_binding(monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = [(321,)]
    cur.fetchone.return_value = (True,)
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def opened(_ds):
        yield ("postgresql", conn, "public")

    monkeypatch.setattr(driver, "open_connection", opened)
    ds = SimpleNamespace(ds_type="postgresql")
    result = driver.cancel_execution(ds, driver.ExecutionBinding(4, "lease", 2))
    assert result.confirmed is True
    assert result.method == "pg_cancel_backend"
    assert cur.execute.call_args_list[0].args[1] == ("gido-adhoc:4:lease:2",)
    assert cur.execute.call_args_list[1].args == ("SELECT pg_cancel_backend(%s)", (321,))


def test_cancel_permission_failure_is_unconfirmed(monkeypatch):
    @contextmanager
    def denied(_ds):
        raise RuntimeError("permission denied for relation pg_stat_activity")
        yield

    monkeypatch.setattr(driver, "open_connection", denied)
    result = driver.cancel_execution(
        SimpleNamespace(ds_type="postgresql"),
        driver.ExecutionBinding(4, "lease", 2),
    )
    assert result.requested is True
    assert result.confirmed is False
    assert "权限不足" in (result.reason or "")


def test_mysql_cancel_uses_control_connection_kill_query(monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = [(812,)]
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def opened(_ds):
        yield ("mysql", conn)

    monkeypatch.setattr(driver, "open_connection", opened)
    result = driver.cancel_run_execution(
        SimpleNamespace(ds_type="doris"), 6, "server-owned-lease"
    )
    assert result.confirmed is True
    assert result.method == "kill_query"
    assert cur.execute.call_args_list[0].args[1] == (
        "/*gido-adhoc:6:server-owned-lease:%",
    )
    assert cur.execute.call_args_list[1].args == ("KILL QUERY 812",)


def test_stale_queued_cancel_follows_concurrent_claim():
    factory = _factory()
    cancel_db = factory()
    claim_db = factory()
    row = AdhocRun(workspace_id=1, source="probe", status="queued")
    cancel_db.add(row)
    cancel_db.commit()
    stale = cancel_db.query(AdhocRun).filter(AdhocRun.id == row.id).one()

    claimed = claim_db.query(AdhocRun).filter(AdhocRun.id == row.id).one()
    claimed.status = "running"
    claimed.worker_id = worker._worker_name
    claimed.lease_token = "claimed-token"
    claim_db.commit()

    saved = worker.request_cancel(cancel_db, stale)
    assert saved.status == "cancel_requested"

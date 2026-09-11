# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""实例中心的运行类型分离，以及人工置成功不被引擎采集覆盖。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-run-type")

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, operation, workflow as workflow_api
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models import rbac_models  # noqa: F401
from app.models.rbac_models import Role
from app.models.workspace import (
    AlertEvent,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
    WorkspaceMember,
)
from app.services.instance_override import is_status_pinned
from app.services.rbac_seed import run_rbac_bootstrap
from app.services.workflow_trigger_display import classify_run_type


# (trigger_type, dolphin_command_type) → 期望的运行类型
_CASES = [
    ("schedule|ds:1", "SCHEDULER", "schedule"),
    ("schedule", None, "schedule"),
    ("manual|ds:2", "COMPLEMENT_DATA", "backfill"),
    ("backfill", None, "backfill"),
    ("batch", None, "backfill"),
    ("manual|ds:3", "REPEAT_RUNNING", "rerun"),
    ("rerun", None, "rerun"),
    ("manual|ds:4", "START_PROCESS", "manual"),
    ("manual", None, "manual"),
    ("local", None, "manual"),
    # commandType 认不出来时退回 trigger_type
    ("schedule|ds:5", "SOME_FUTURE_COMMAND", "schedule"),
    # 两边都认不出来，兜底进手动视图而不是凭空消失
    ("weird-thing", None, "manual"),
    (None, None, "manual"),
]


@pytest.mark.parametrize("trigger,cmd,expected", _CASES)
def test_classify_run_type(trigger, cmd, expected):
    assert classify_run_type(trigger, cmd) == expected


def _client():
    from app.api import operation as operation_mod

    operation_mod._run_type_count_local.clear()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    run_rbac_bootstrap(db)
    roles = {r.code: r for r in db.query(Role).all()}
    admin = User(
        username="admin", email="admin@gido.com", hashed_password=get_password_hash("admin123"),
        is_admin=True, is_active=True, role_id=roles["platform_admin"].id,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    ws = Workspace(name="infras", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    wf = Workflow(name="ads_daily", workspace_id=ws.id, is_active=True, status="published")
    db.add(wf)
    db.commit()
    db.refresh(wf)
    for trigger, cmd, _expected in _CASES:
        db.add(WorkflowInstance(
            workflow_id=wf.id, status="success", trigger_type=trigger,
            dolphin_command_type=cmd, created_at=datetime.utcnow(),
        ))
    db.commit()
    ws_id, wf_id = int(ws.id), int(wf.id)
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(operation.router, prefix="/api")
    app.include_router(workflow_api.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), SessionLocal, ws_id, wf_id


def _auth(c: TestClient) -> dict:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _list(c, h, ws_id, **params):
    r = c.get("/api/operation/instances", headers=h, params={"workspace_id": ws_id, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_run_type_tabs_partition_every_instance():
    """每条实例恰好落进一个标签页，四个标签页加起来等于全部。"""
    c, _SessionLocal, ws_id, _wf_id = _client()
    h = _auth(c)

    everything = _list(c, h, ws_id)
    counts = everything["run_type_counts"]
    assert sum(counts.values()) == everything["total"] == len(_CASES)

    seen = set()
    for run_type in ("schedule", "backfill", "rerun", "manual"):
        body = _list(c, h, ws_id, run_type=run_type)
        assert body["total"] == counts[run_type]
        ids = {i["id"] for i in body["items"]}
        assert not (ids & seen), f"{run_type} 与其他标签页重叠"
        seen |= ids
        assert all(i["run_type"] == run_type for i in body["items"])
    assert len(seen) == len(_CASES)


def test_sql_filter_matches_python_classifier():
    """标签页的 SQL 判定必须和展示用的 classify_run_type 完全一致。"""
    c, _SessionLocal, ws_id, _wf_id = _client()
    h = _auth(c)
    for run_type in ("schedule", "backfill", "rerun", "manual"):
        body = _list(c, h, ws_id, run_type=run_type)
        expected = sum(1 for t, cmd, exp in _CASES if exp == run_type)
        assert body["total"] == expected, f"{run_type} 期望 {expected} 条，实际 {body['total']}"


def test_unknown_run_type_rejected():
    c, _SessionLocal, ws_id, _wf_id = _client()
    h = _auth(c)
    r = c.get("/api/operation/instances", headers=h, params={"workspace_id": ws_id, "run_type": "nope"})
    assert r.status_code == 400


def test_run_type_counts_ignore_run_type_but_respect_other_filters():
    """切标签时数字不该跳：计数用的是除运行类型外的同一批过滤条件。"""
    c, SessionLocal, ws_id, wf_id = _client()
    h = _auth(c)
    db = SessionLocal()
    db.add(WorkflowInstance(
        workflow_id=wf_id, status="failed", trigger_type="schedule",
        dolphin_command_type="SCHEDULER", created_at=datetime.utcnow(),
    ))
    db.commit()
    db.close()

    all_counts = _list(c, h, ws_id)["run_type_counts"]
    on_tab = _list(c, h, ws_id, run_type="manual")["run_type_counts"]
    assert on_tab == all_counts

    failed_only = _list(c, h, ws_id, status="failed")["run_type_counts"]
    assert failed_only["schedule"] == 1
    assert failed_only["manual"] == 0


def _failed_instance(SessionLocal, wf_id: int) -> int:
    db = SessionLocal()
    inst = WorkflowInstance(
        workflow_id=wf_id, status="failed", trigger_type="schedule",
        dolphin_command_type="SCHEDULER", business_date="2026-09-10",
        started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    inst_id = int(inst.id)
    db.close()
    return inst_id


def test_mark_success_pins_status_and_closes_alert():
    c, SessionLocal, ws_id, wf_id = _client()
    h = _auth(c)
    inst_id = _failed_instance(SessionLocal, wf_id)
    db = SessionLocal()
    db.add(AlertEvent(
        workspace_id=ws_id, workflow_id=wf_id, workflow_instance_id=inst_id,
        alert_type="failed", level="error", status="open",
        dedupe_key=f"instance:{inst_id}", message="实例执行失败",
    ))
    db.commit()
    db.close()

    r = c.post(
        f"/api/workflows/{wf_id}/instances/{inst_id}/override-status",
        headers=h, json={"status": "success", "reason": "数据已手工修复"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "success"

    db = SessionLocal()
    inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == inst_id).first()
    assert inst.status == "success"
    assert inst.status_override == "success"
    assert inst.override_reason == "数据已手工修复"
    assert is_status_pinned(inst)
    alert = db.query(AlertEvent).filter(AlertEvent.workflow_instance_id == inst_id).first()
    assert alert.status == "resolved"
    db.close()


def test_running_instance_cannot_be_overridden():
    c, SessionLocal, _ws_id, wf_id = _client()
    h = _auth(c)
    db = SessionLocal()
    inst = WorkflowInstance(workflow_id=wf_id, status="running", trigger_type="schedule")
    db.add(inst)
    db.commit()
    db.refresh(inst)
    inst_id = int(inst.id)
    db.close()

    r = c.post(
        f"/api/workflows/{wf_id}/instances/{inst_id}/override-status",
        headers=h, json={"status": "success"},
    )
    assert r.status_code == 400


def test_invalid_override_status_rejected():
    c, SessionLocal, _ws_id, wf_id = _client()
    h = _auth(c)
    inst_id = _failed_instance(SessionLocal, wf_id)
    r = c.post(
        f"/api/workflows/{wf_id}/instances/{inst_id}/override-status",
        headers=h, json={"status": "running"},
    )
    assert r.status_code == 400


def test_clear_override_restores_engine_authority():
    c, SessionLocal, _ws_id, wf_id = _client()
    h = _auth(c)
    inst_id = _failed_instance(SessionLocal, wf_id)
    c.post(
        f"/api/workflows/{wf_id}/instances/{inst_id}/override-status",
        headers=h, json={"status": "success"},
    ).raise_for_status()

    r = c.delete(f"/api/workflows/{wf_id}/instances/{inst_id}/override-status", headers=h)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == inst_id).first()
    assert not is_status_pinned(inst)
    assert inst.override_by is None
    db.close()


class _StubDsClient:
    """引擎仍然认为这次运行是失败的。"""

    def get_instance_status(self, project_code, instance_id):
        return {"state": "FAILURE", "state_dw": "failed", "command_type": "SCHEDULER"}

    def list_task_instances_all(self, project_code, instance_id):
        return []


def test_collection_does_not_undo_manual_success():
    """置成功之后，引擎照旧回报失败，但采集既不能改回状态，也不能重新开告警。"""
    c, SessionLocal, _ws_id, wf_id = _client()
    h = _auth(c)
    inst_id = _failed_instance(SessionLocal, wf_id)
    db = SessionLocal()
    inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == inst_id).first()
    inst.scheduler_project_id = "777"
    inst.scheduler_instance_id = "88888"
    db.commit()
    db.close()

    c.post(
        f"/api/workflows/{wf_id}/instances/{inst_id}/override-status",
        headers=h, json={"status": "success", "reason": "旁路修复"},
    ).raise_for_status()

    from app.services.dolphin_instance_sync import patch_instances_from_ds_detail

    db = SessionLocal()
    patch_instances_from_ds_detail(db, _StubDsClient(), limit=50)
    inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == inst_id).first()
    assert inst.status == "success"
    assert inst.status_override == "success"
    # 引擎快照照常更新，只是不再决定状态
    assert inst.scheduler_state_raw == "FAILURE"
    assert db.query(AlertEvent).filter(
        AlertEvent.workflow_instance_id == inst_id,
        AlertEvent.status == "open",
    ).count() == 0
    db.close()


def test_scheduler_lost_does_not_touch_pinned_instance():
    from app.services.dolphin_instance_sync import _mark_instance_scheduler_lost

    inst = WorkflowInstance(workflow_id=1, status="running")
    assert _mark_instance_scheduler_lost(inst, "调度实例不存在") is True
    assert inst.status == "failed"

    pinned = WorkflowInstance(workflow_id=1, status="running", status_override="success")
    assert _mark_instance_scheduler_lost(pinned, "调度实例不存在") is False
    assert pinned.status == "running"


def test_override_surfaces_in_instance_list():
    c, SessionLocal, ws_id, wf_id = _client()
    h = _auth(c)
    inst_id = _failed_instance(SessionLocal, wf_id)
    c.post(
        f"/api/workflows/{wf_id}/instances/{inst_id}/override-status",
        headers=h, json={"status": "success", "reason": "上游补数已完成"},
    ).raise_for_status()

    body = _list(c, h, ws_id, page_size=100)
    row = next(i for i in body["items"] if i["id"] == inst_id)
    assert row["status"] == "success"
    assert row["status_override"] == "success"
    assert row["override_reason"] == "上游补数已完成"
    assert row["override_at"]

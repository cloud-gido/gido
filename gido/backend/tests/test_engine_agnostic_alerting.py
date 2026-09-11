# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""告警闭环不能只在 Dolphin 采集路径上成立：本地/APS 执行与引擎回调都要进告警中心。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-engine-agnostic")

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import AlertEvent, User, Workflow, WorkflowInstance, Workspace
from app.services.scheduler import _raise_or_clear_instance_alert


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    user = User(username="ops", email="ops@gido.test", hashed_password="x", is_admin=True, is_active=True)
    session.add(user)
    session.flush()
    ws = Workspace(name="ops-ws", owner_id=user.id, timezone="Asia/Shanghai")
    session.add(ws)
    session.commit()
    yield session
    session.close()


def _instance(db, status: str) -> WorkflowInstance:
    ws = db.query(Workspace).first()
    wf = Workflow(name="本地工作流", workspace_id=ws.id, is_active=True)
    db.add(wf)
    db.flush()
    inst = WorkflowInstance(
        workflow_id=wf.id,
        status=status,
        trigger_type="schedule",
        business_date="2026-09-11",
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def test_local_schedule_failure_opens_alert(db):
    """DS 未启用（或换成别的引擎）时，本地调度失败同样要出现在告警中心。"""
    inst = _instance(db, "failed")
    with patch("app.services.alert_notification.notify_alert_event", return_value=None):
        _raise_or_clear_instance_alert(db, inst, ["节点 dwd_order: SQL 执行超时"])

    ev = db.query(AlertEvent).filter(AlertEvent.workflow_instance_id == inst.id).one()
    assert ev.status == "open"
    assert ev.alert_type == "failed"
    assert "dwd_order" in (ev.message or "")


def test_local_schedule_success_resolves_previous_alert(db):
    inst = _instance(db, "failed")
    with patch("app.services.alert_notification.notify_alert_event", return_value=None):
        _raise_or_clear_instance_alert(db, inst, ["节点 a: boom"])
        inst.status = "success"
        db.commit()
        _raise_or_clear_instance_alert(db, inst, [])

    ev = (
        db.query(AlertEvent)
        .filter(AlertEvent.workflow_instance_id == inst.id, AlertEvent.alert_type == "failed")
        .one()
    )
    assert ev.status == "resolved"
    assert ev.resolved_at is not None


def test_local_schedule_alert_failure_does_not_break_run(db):
    """告警链路异常不能把已完成的实例状态带崩。"""
    inst = _instance(db, "failed")
    with patch("app.services.alert_center.open_instance_alert", side_effect=RuntimeError("alert down")):
        _raise_or_clear_instance_alert(db, inst, ["节点 a: boom"])
    assert db.query(AlertEvent).count() == 0


def test_callback_rejected_when_internal_token_unset():
    """未配置 INTERNAL_TOKEN 时回调必须拒绝，否则任何人都能伪造实例状态与告警。"""
    from app.api.scheduler import dolphin_scheduler_callback
    from app.core.config import settings

    original = settings.INTERNAL_TOKEN
    settings.INTERNAL_TOKEN = ""
    try:
        with pytest.raises(HTTPException) as exc:
            dolphin_scheduler_callback({"processInstanceId": 1, "state": "FAILURE"}, x_internal_token="")
        assert exc.value.status_code == 503
    finally:
        settings.INTERNAL_TOKEN = original


def test_callback_rejects_wrong_token():
    from app.api.scheduler import dolphin_scheduler_callback

    with pytest.raises(HTTPException) as exc:
        dolphin_scheduler_callback({"processInstanceId": 1, "state": "FAILURE"}, x_internal_token="nope")
    assert exc.value.status_code == 401

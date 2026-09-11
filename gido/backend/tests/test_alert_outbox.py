# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
告警出站箱：写入路径不得同步打 Webhook。

采集 / SLA 打开告警时只把事件标成 pending；真正推飞书由 dispatch_pending_notifications
负责。这是业界成熟的 outbox 做法——投递抖动不能拖慢实例账本，更不能占着采集锁。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    AlertNotificationConfig,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
)
from app.services.alert_center import open_instance_alert
from app.services.alert_notification import (
    FORCE_NOTIFY_SENTINEL,
    dispatch_pending_notifications,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    user = User(
        username="outbox-admin",
        email="outbox@gido.test",
        hashed_password="x",
        is_admin=True,
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.add(Workspace(name="outbox-ws", owner_id=user.id, timezone="Asia/Shanghai"))
    session.commit()
    yield session
    session.close()


def _cfg(db, ws_id: int, url: str = "https://open.feishu.cn/open-apis/bot/v2/hook/x"):
    db.add(
        AlertNotificationConfig(
            workspace_id=ws_id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url=url,
            notify_cooldown_minutes=0,
        )
    )
    db.commit()


def test_open_alert_does_not_call_webhook(db):
    ws = db.query(Workspace).first()
    wf = Workflow(workspace_id=ws.id, name="ob-wf", dag_config={"nodes": []}, status="published")
    db.add(wf)
    db.flush()
    _cfg(db, ws.id)
    inst = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    db.add(inst)
    db.flush()

    with patch("app.services.alert_notification._post_json") as post:
        ev = open_instance_alert(db, workflow_instance=inst, notify=True)
        db.commit()

    assert post.call_count == 0
    assert ev.notification_status == "pending"
    assert ev.notify_next_retry_at is not None


def test_dispatch_delivers_pending_alert(db):
    ws = db.query(Workspace).first()
    wf = Workflow(workspace_id=ws.id, name="ob-wf2", dag_config={"nodes": []}, status="published")
    db.add(wf)
    db.flush()
    _cfg(db, ws.id, "https://open.feishu.cn/open-apis/bot/v2/hook/y")
    inst = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    db.add(inst)
    db.flush()

    with patch("app.services.alert_notification._post_json") as post:
        ev = open_instance_alert(db, workflow_instance=inst, notify=True)
        db.commit()
        assert post.call_count == 0
        stats = dispatch_pending_notifications(db)
        db.refresh(ev)

    assert stats["first_delivery"] == 1
    assert post.call_count == 1
    assert ev.notification_status == "sent"


def test_force_notify_sentinel_survives_until_dispatch(db):
    ws = db.query(Workspace).first()
    wf = Workflow(workspace_id=ws.id, name="ob-rec", dag_config={"nodes": []}, status="published")
    db.add(wf)
    db.flush()
    # min_severity=error，info 级恢复卡片必须 force 才能发出去
    _cfg(db, ws.id, "https://open.feishu.cn/open-apis/bot/v2/hook/z")
    inst = WorkflowInstance(workflow_id=wf.id, status="success", trigger_type="schedule")
    db.add(inst)
    db.flush()

    with patch("app.services.alert_notification._post_json") as post:
        ev = open_instance_alert(
            db,
            workflow_instance=inst,
            alert_type="recovered",
            level="info",
            message="已恢复",
            notify=True,
            force_notify=True,
        )
        db.commit()
        assert ev.notify_pending_channels == FORCE_NOTIFY_SENTINEL
        assert post.call_count == 0
        dispatch_pending_notifications(db)
        db.refresh(ev)

    assert post.call_count == 1
    assert ev.notification_status == "sent"
    assert ev.notify_pending_channels is None


def test_scheduler_job_interval_is_outbox_cadence():
    """投递间隔必须短到「准实时」，又不能和采集抢成一秒一轮。"""
    from app.services import scheduler as sch

    assert 3 <= sch._NOTIFY_RETRY_INTERVAL_SEC <= 15

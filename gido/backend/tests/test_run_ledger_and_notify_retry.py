# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""运行台账（一次运行一行）、采集水位线翻页、通知重投。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-run-ledger")

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    AlertEvent,
    AlertNotificationConfig,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
)
from app.services.alert_notification import retry_pending_notifications
from app.services.dolphin_instance_sync import _advance_sync_cursor, _get_sync_cursor, _walk_pages_until_watermark


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    user = User(username="ops", email="ops@gido.test", hashed_password="x", is_admin=True, is_active=True)
    session.add(user)
    session.flush()
    session.add(Workspace(name="ws", owner_id=user.id, timezone="Asia/Shanghai"))
    session.commit()
    yield session
    session.close()


def _workflow(db) -> Workflow:
    ws = db.query(Workspace).first()
    wf = Workflow(name="wf", workspace_id=ws.id, is_active=True)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


def test_run_key_is_unique_across_instances(db):
    """同一次引擎运行不能落两行，否则告警和运行统计都会重复。"""
    wf = _workflow(db)
    for _ in range(2):
        db.add(WorkflowInstance(workflow_id=wf.id, status="failed", scheduler_run_key="dolphin:1:2:300"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_rerun_keeps_original_run_as_its_own_row(db):
    """重跑新开一行并指回原实例，原来那次运行的结果不被覆盖。"""
    wf = _workflow(db)
    first = WorkflowInstance(
        workflow_id=wf.id,
        status="failed",
        trigger_type="schedule|ds:100",
        scheduler_instance_id="100",
        scheduler_run_key="dolphin:1:2:100",
        business_date="2026-09-11",
    )
    db.add(first)
    db.commit()
    db.refresh(first)

    rerun = WorkflowInstance(
        workflow_id=wf.id,
        parent_instance_id=first.id,
        status="running",
        trigger_type="rerun",
        scheduler_instance_id="101",
        scheduler_run_key="dolphin:1:2:101",
        business_date=first.business_date,
    )
    db.add(rerun)
    db.commit()

    db.refresh(first)
    assert first.status == "failed"
    assert first.scheduler_instance_id == "100"
    assert rerun.parent_instance_id == first.id
    assert db.query(WorkflowInstance).filter(WorkflowInstance.workflow_id == wf.id).count() == 2


def test_sync_cursor_only_moves_forward(db):
    cursor = _get_sync_cursor(db, "dolphin", 11, 22)
    db.commit()
    _advance_sync_cursor(cursor, 500)
    assert cursor.last_instance_id == 500
    _advance_sync_cursor(cursor, 120)
    assert cursor.last_instance_id == 500


def test_sync_cursor_is_per_definition(db):
    a = _get_sync_cursor(db, "dolphin", 11, 22)
    _advance_sync_cursor(a, 9)
    db.commit()
    b = _get_sync_cursor(db, "dolphin", 11, 33)
    db.commit()
    assert b.id != a.id
    assert (b.last_instance_id or 0) == 0


class _FakeDs:
    """升序排列的 Dolphin 列表：最新的实例在最后一页。"""

    def __init__(self, pages: dict[int, list[dict]]):
        self.pages = pages
        self.requested: list[int] = []

    def list_process_instances_page(self, project_code, *, page_no=1, **kwargs):
        self.requested.append(page_no)
        return {
            "rows": self.pages.get(page_no, []),
            "total_page": len(self.pages),
            "total": sum(len(v) for v in self.pages.values()),
        }


def _rows(*ids: int) -> list[dict]:
    return [{"id": i, "state": "SUCCESS"} for i in ids]


def test_walk_pages_reaches_middle_pages_until_watermark():
    """中间页不能被跳过：从最新一侧往回翻，直到追平水位线。"""
    ds = _FakeDs({1: _rows(1, 2), 2: _rows(3, 4), 3: _rows(5, 6), 4: _rows(7, 8)})
    rows = _walk_pages_until_watermark(ds, 1, 2, watermark=4, page_size=2)

    got = sorted(r["id"] for r in rows)
    # 旧实现只拉首页和末页，会漏掉第 3 页的 5、6
    assert [5, 6, 7, 8] == [i for i in got if i > 4]
    # 追平后就停，不会把整张列表重新翻一遍
    assert ds.requested.count(1) == 1


def test_walk_pages_stops_when_nothing_new():
    ds = _FakeDs({1: _rows(1, 2), 2: _rows(3, 4), 3: _rows(5, 6)})
    _walk_pages_until_watermark(ds, 1, 2, watermark=99, page_size=2)
    # 首页、末页各一次，再看一页发现都低于水位线就停
    assert len(ds.requested) <= 3


def _failed_alert(db, channels: str = "lark") -> AlertEvent:
    ws = db.query(Workspace).first()
    db.add(
        AlertNotificationConfig(
            workspace_id=ws.id,
            enabled=True,
            min_severity="error",
            lark_enabled=True,
            lark_webhook_url="https://open.feishu.cn/hook/x",
        )
    )
    ev = AlertEvent(
        workspace_id=ws.id,
        alert_type="failed",
        level="error",
        severity="error",
        status="open",
        message="boom",
        notification_status="failed",
        notify_attempts=1,
        notify_pending_channels=channels,
        notify_next_retry_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def test_retry_resends_only_failed_channel(db):
    ev = _failed_alert(db)
    with patch("app.services.alert_notification._post_json") as post:
        stats = retry_pending_notifications(db)

    assert stats == {"due": 1, "first_delivery": 0, "recovered": 1, "still_failing": 0}
    assert post.call_count == 1
    db.refresh(ev)
    assert ev.notification_status == "sent"
    assert ev.notify_pending_channels is None
    assert ev.notify_next_retry_at is None


def test_retry_backs_off_and_eventually_gives_up(db):
    ev = _failed_alert(db)
    ev.notify_attempts = 4
    db.commit()

    with patch("app.services.alert_notification._post_json", side_effect=RuntimeError("webhook 503")):
        stats = retry_pending_notifications(db)

    assert stats["still_failing"] == 1
    db.refresh(ev)
    assert ev.notification_status == "failed"
    assert "webhook 503" in (ev.notify_last_error or "")
    # 第 5 次仍失败就不再排下一次，留在列表里等人处理
    assert ev.notify_next_retry_at is None


def test_retry_skips_channel_that_was_turned_off(db):
    ev = _failed_alert(db, channels="wecom")
    with patch("app.services.alert_notification._post_json") as post:
        retry_pending_notifications(db)

    post.assert_not_called()
    db.refresh(ev)
    assert ev.notify_pending_channels is None

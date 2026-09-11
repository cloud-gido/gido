# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""基线巡检：承诺完成时间未达成、运行超时，以及晚到后自动关闭。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-sla")

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    AlertEvent,
    User,
    Workflow,
    WorkflowInstance,
    WorkflowSlaRule,
    Workspace,
)
from app.services.sla_monitor import _due_business_dates, evaluate_sla

_TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture(autouse=True)
def no_push():
    """基线用例只验证告警入库与关闭，推送通道单独测。"""
    with patch("app.services.alert_notification.notify_alert_event", return_value=None):
        yield


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
    wf = Workflow(name="ads_daily", workspace_id=ws.id, is_active=True, status="published")
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


def _rule(db, wf, **kw) -> WorkflowSlaRule:
    rule = WorkflowSlaRule(
        workspace_id=wf.workspace_id,
        workflow_id=wf.id,
        enabled=True,
        expect_finish_offset_days=kw.pop("offset_days", 1),
        **kw,
    )
    db.add(rule)
    db.commit()
    return rule


def _yesterday_biz() -> str:
    """承诺 T+1 的场景里，昨天的业务日期今天早上就该产出了。"""
    today_local = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(_TZ).date()
    return (today_local - timedelta(days=1)).strftime("%Y-%m-%d")


def test_missed_deadline_opens_alert_without_any_instance(db):
    """压根没被调度起来：没有实例也要告警，这是只看失败时最容易漏的事故。"""
    wf = _workflow(db)
    _rule(db, wf, expect_finish_time="00:01")

    stats = evaluate_sla(db)

    assert stats["missed_deadline"] == 1
    ev = db.query(AlertEvent).filter(AlertEvent.alert_type == "sla").one()
    assert ev.status == "open"
    assert ev.workflow_instance_id is None
    assert _yesterday_biz() in (ev.message or "")
    assert "没有成功的运行" in (ev.message or "")


def test_missed_deadline_is_idempotent_per_business_date(db):
    wf = _workflow(db)
    _rule(db, wf, expect_finish_time="00:01")

    evaluate_sla(db)
    evaluate_sla(db)

    assert db.query(AlertEvent).filter(AlertEvent.alert_type == "sla").count() == 1


def test_success_before_deadline_does_not_alert(db):
    wf = _workflow(db)
    _rule(db, wf, expect_finish_time="00:01")
    db.add(
        WorkflowInstance(
            workflow_id=wf.id,
            status="success",
            business_date=_yesterday_biz(),
            started_at=datetime.utcnow() - timedelta(hours=2),
            finished_at=datetime.utcnow() - timedelta(hours=1),
        )
    )
    db.commit()

    stats = evaluate_sla(db)

    assert stats["missed_deadline"] == 0
    assert db.query(AlertEvent).count() == 0


def test_running_instance_reported_as_still_running(db):
    wf = _workflow(db)
    _rule(db, wf, expect_finish_time="00:01")
    db.add(
        WorkflowInstance(
            workflow_id=wf.id,
            status="running",
            business_date=_yesterday_biz(),
            started_at=datetime.utcnow() - timedelta(minutes=10),
        )
    )
    db.commit()

    evaluate_sla(db)

    ev = db.query(AlertEvent).filter(AlertEvent.alert_type == "sla").one()
    assert "仍在运行中" in (ev.message or "")


def test_late_success_resolves_missed_deadline_alert(db):
    """晚到也算到了：补跑成功后基线告警自动关闭，不用人去点。"""
    wf = _workflow(db)
    _rule(db, wf, expect_finish_time="00:01")
    evaluate_sla(db)
    ev = db.query(AlertEvent).filter(AlertEvent.alert_type == "sla").one()
    assert ev.status == "open"

    db.add(
        WorkflowInstance(
            workflow_id=wf.id,
            status="success",
            business_date=_yesterday_biz(),
            started_at=datetime.utcnow() - timedelta(minutes=20),
            finished_at=datetime.utcnow(),
        )
    )
    db.commit()

    evaluate_sla(db)

    db.refresh(ev)
    assert ev.status == "resolved"
    assert ev.resolved_at is not None


def test_long_running_instance_opens_timeout_alert(db):
    wf = _workflow(db)
    _rule(db, wf, max_duration_minutes=30)
    inst = WorkflowInstance(
        workflow_id=wf.id,
        status="running",
        business_date="2026-09-11",
        started_at=datetime.utcnow() - timedelta(minutes=95),
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)

    stats = evaluate_sla(db)

    assert stats["long_running"] == 1
    ev = db.query(AlertEvent).filter(AlertEvent.alert_type == "timeout").one()
    assert ev.workflow_instance_id == inst.id
    assert ev.level == "warning"
    assert "95 分钟" in (ev.message or "")


def test_instance_within_limit_is_not_timeout(db):
    wf = _workflow(db)
    _rule(db, wf, max_duration_minutes=120)
    db.add(
        WorkflowInstance(
            workflow_id=wf.id,
            status="running",
            business_date="2026-09-11",
            started_at=datetime.utcnow() - timedelta(minutes=30),
        )
    )
    db.commit()

    stats = evaluate_sla(db)

    assert stats["long_running"] == 0
    assert db.query(AlertEvent).count() == 0


def test_timeout_alert_resolves_when_slow_run_finally_succeeds(db):
    from app.services.alert_center import resolve_instance_alerts_on_recovery

    wf = _workflow(db)
    _rule(db, wf, max_duration_minutes=30)
    inst = WorkflowInstance(
        workflow_id=wf.id,
        status="running",
        business_date="2026-09-11",
        started_at=datetime.utcnow() - timedelta(minutes=95),
    )
    db.add(inst)
    db.commit()
    evaluate_sla(db)
    ev = db.query(AlertEvent).filter(AlertEvent.alert_type == "timeout").one()

    inst.status = "success"
    inst.finished_at = datetime.utcnow()
    db.commit()
    resolve_instance_alerts_on_recovery(db, inst)
    db.commit()

    db.refresh(ev)
    assert ev.status == "resolved"
    # 跑得慢但最终成功，不该再推一条「已恢复」卡片
    assert db.query(AlertEvent).filter(AlertEvent.alert_type == "recovered").count() == 0


def test_disabled_rule_is_skipped(db):
    wf = _workflow(db)
    rule = _rule(db, wf, expect_finish_time="00:01")
    rule.enabled = False
    db.commit()

    stats = evaluate_sla(db)

    assert stats == {"rules_checked": 0, "missed_deadline": 0, "long_running": 0}
    assert db.query(AlertEvent).count() == 0


def test_inactive_workflow_is_skipped(db):
    wf = _workflow(db)
    _rule(db, wf, expect_finish_time="00:01")
    wf.is_active = False
    db.commit()

    stats = evaluate_sla(db)

    assert stats["rules_checked"] == 0
    assert db.query(AlertEvent).count() == 0


def test_due_dates_exclude_deadline_not_yet_reached():
    """承诺时间还没到就不该催，否则每天早上都是一片假告警。"""
    # 北京时间 2026-09-11 08:00，承诺 T+1 的 09:30
    now_utc = datetime(2026, 9, 11, 0, 0)
    due = _due_business_dates(now_utc, (9, 30), 1, _TZ)

    # 09-10 的承诺点是 09-11 09:30，还没到
    assert "2026-09-10" not in due
    # 09-09 的承诺点是 09-10 09:30，已过且在回溯窗口内
    assert "2026-09-09" in due


def test_due_dates_stop_at_lookback_window():
    """首次启用基线时不能把历史日期全部刷成告警。"""
    now_utc = datetime(2026, 9, 11, 0, 0)
    due = _due_business_dates(now_utc, (9, 30), 1, _TZ)

    # 回溯窗口 30 小时，只覆盖最近一两个业务日期
    assert len(due) <= 2
    assert "2026-09-01" not in due

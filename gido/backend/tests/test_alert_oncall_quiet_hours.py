# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""值班表与静默时段：告警要找对人，且夜里只放过够严重的——压住不等于丢掉。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-oncall")

from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import get_password_hash
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    AlertEvent,
    AlertNotificationConfig,
    AlertOnCallShift,
    User,
    Workspace,
)
from app.services.alert_notification import notify_alert_event, retry_pending_notifications
from app.services.alert_oncall import (
    in_quiet_hours,
    on_call_label,
    quiet_hours_decision,
    quiet_hours_end_utc,
    resolve_on_call,
    workspace_tz,
)

# 上海 = UTC+8，所有断言都以上海本地时间来读
_TZ_SH = "Asia/Shanghai"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def _ws(db, tz: str = _TZ_SH) -> Workspace:
    ws = Workspace(name="infras", timezone=tz)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


def _user(db, name: str) -> User:
    u = User(
        username=name, email=f"{name}@gido.com",
        hashed_password=get_password_hash("x"), is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _shift(db, ws, user, **kwargs) -> AlertOnCallShift:
    defaults = dict(weekdays="*", start_time="00:00", end_time="24:00", enabled=True)
    defaults.update(kwargs)
    s = AlertOnCallShift(workspace_id=ws.id, user_id=user.id, **defaults)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _utc(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm)


# ---------- 值班表 ----------

def test_no_roster_means_nobody_on_call(db):
    ws = _ws(db)
    assert resolve_on_call(db, ws.id) == []
    assert on_call_label(db, ws.id) == "未排班"


def test_day_shift_only_covers_its_window(db):
    ws = _ws(db)
    day = _user(db, "day_person")
    _shift(db, ws, day, start_time="09:00", end_time="18:00")

    # 上海 12:00 → UTC 04:00，在班
    assert [u.username for u in resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 4))] == ["day_person"]
    # 上海 22:00 → UTC 14:00，不在班
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 14)) == []


def test_overnight_shift_spans_midnight(db):
    """22:00–06:00 的夜班要在零点两侧都成立，这是最容易写错的一段。"""
    ws = _ws(db)
    night = _user(db, "night_person")
    _shift(db, ws, night, start_time="22:00", end_time="06:00")

    # 上海 23:00（UTC 15:00）
    assert [u.username for u in resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 15))] == ["night_person"]
    # 上海次日 03:00（UTC 19:00 前一天）
    assert [u.username for u in resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 19))] == ["night_person"]
    # 上海 12:00，不在班
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 4)) == []


def test_weekday_restriction_respected(db):
    ws = _ws(db)
    weekday_person = _user(db, "weekday_person")
    # 2026-09-11 是周五（ISO 5）
    _shift(db, ws, weekday_person, weekdays="1,2,3,4,5", start_time="09:00", end_time="18:00")

    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 4)) != []   # 周五
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 12, 4)) == []   # 周六


def test_overnight_shift_belongs_to_the_day_it_starts(db):
    """周五 22:00 的夜班，凌晨那一段算周五的班，不该让周五 00:30 也算在班。"""
    ws = _ws(db)
    p = _user(db, "fri_night")
    _shift(db, ws, p, weekdays="5", start_time="22:00", end_time="06:00")

    # 周五 23:00 上海 → 在班
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 15)) != []
    # 周六 03:00 上海（属于周五那班）→ 在班
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 19)) != []
    # 周五 03:00 上海（属于周四那班）→ 不在班
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 10, 19)) == []


def test_disabled_shift_ignored(db):
    ws = _ws(db)
    p = _user(db, "off_duty")
    _shift(db, ws, p, enabled=False)
    assert resolve_on_call(db, ws.id, at=_utc(2026, 9, 11, 4)) == []


def test_multiple_on_call_people_listed_in_order(db):
    ws = _ws(db)
    a = _user(db, "primary")
    b = _user(db, "backup")
    _shift(db, ws, a)
    _shift(db, ws, b)
    assert on_call_label(db, ws.id, at=_utc(2026, 9, 11, 4)) == "primary、backup"


def test_new_alert_assigned_to_on_call_person(db):
    from app.models.workspace import Workflow, WorkflowInstance
    from app.services.alert_center import open_instance_alert

    ws = _ws(db)
    p = _user(db, "duty")
    _shift(db, ws, p)
    wf = Workflow(name="ads_daily", workspace_id=ws.id, status="published")
    db.add(wf)
    db.commit()
    db.refresh(wf)
    inst = WorkflowInstance(workflow_id=wf.id, status="failed", trigger_type="schedule")
    db.add(inst)
    db.commit()
    db.refresh(inst)

    event = open_instance_alert(db, workflow_instance=inst, message="失败了", notify=False)
    db.commit()
    assert event.assignee_id == p.id


# ---------- 静默时段 ----------

def _quiet_cfg(db, ws, **kwargs) -> AlertNotificationConfig:
    defaults = dict(
        enabled=True, min_severity="info", lark_enabled=True,
        lark_webhook_url="https://example.invalid/hook",
        quiet_hours_enabled=True, quiet_hours_start="23:00", quiet_hours_end="08:00",
        quiet_hours_min_severity="critical",
        notify_armed_at=datetime(2020, 1, 1),
    )
    defaults.update(kwargs)
    cfg = AlertNotificationConfig(workspace_id=ws.id, **defaults)
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


def test_quiet_hours_window_crosses_midnight(db):
    ws = _ws(db)
    cfg = _quiet_cfg(db, ws)
    tz = workspace_tz(db, ws.id)

    assert in_quiet_hours(cfg, tz, at=_utc(2026, 9, 11, 15)) is True    # 上海 23:00
    assert in_quiet_hours(cfg, tz, at=_utc(2026, 9, 11, 19)) is True    # 上海次日 03:00
    assert in_quiet_hours(cfg, tz, at=_utc(2026, 9, 11, 4)) is False    # 上海 12:00


def test_quiet_hours_end_is_the_next_occurrence(db):
    ws = _ws(db)
    cfg = _quiet_cfg(db, ws)
    tz = workspace_tz(db, ws.id)
    # 上海 2026-09-11 23:00 → 下一个 08:00 是 09-12 08:00 上海 = 09-12 00:00 UTC
    assert quiet_hours_end_utc(cfg, tz, at=_utc(2026, 9, 11, 15)) == datetime(2026, 9, 12, 0, 0)


def test_severe_enough_alert_goes_out_during_quiet_hours(db):
    ws = _ws(db)
    cfg = _quiet_cfg(db, ws)
    deferred, _until = quiet_hours_decision(
        db, cfg, workspace_id=ws.id, severity="critical", at=_utc(2026, 9, 11, 15)
    )
    assert deferred is False


def test_lesser_alert_is_deferred_during_quiet_hours(db):
    ws = _ws(db)
    cfg = _quiet_cfg(db, ws)
    deferred, until = quiet_hours_decision(
        db, cfg, workspace_id=ws.id, severity="error", at=_utc(2026, 9, 11, 15)
    )
    assert deferred is True
    assert until == datetime(2026, 9, 12, 0, 0)


def test_quiet_hours_disabled_never_defers(db):
    ws = _ws(db)
    cfg = _quiet_cfg(db, ws, quiet_hours_enabled=False)
    deferred, _until = quiet_hours_decision(
        db, cfg, workspace_id=ws.id, severity="info", at=_utc(2026, 9, 11, 15)
    )
    assert deferred is False


def test_deferred_alert_is_held_then_sent_by_retry_job(db):
    """压住的告警必须仍然会被推出去，只是晚一点——这是「不丢」的核心保证。"""
    ws = _ws(db)
    _quiet_cfg(db, ws)
    event = AlertEvent(
        workspace_id=ws.id, alert_type="failed", level="error", severity="error",
        status="open", message="夜里失败了", notification_status="pending",
        dedupe_key="instance:1",
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    quiet_night = datetime(2026, 9, 11, 15)  # 上海 23:00
    with patch("app.services.alert_notification.datetime") as dt, \
         patch("app.services.alert_notification._post_json") as send:
        dt.utcnow.return_value = quiet_night
        with patch("app.services.alert_oncall.datetime") as dt2:
            dt2.utcnow.return_value = quiet_night
            result = notify_alert_event(db, event)
        assert send.call_count == 0, "静默时段内不该发出去"
    db.commit()

    assert result["skipped"] == "quiet_hours"
    assert event.notification_status == "deferred"
    assert event.notify_pending_channels == "lark"
    assert event.notify_next_retry_at == datetime(2026, 9, 12, 0, 0)

    # 到点了：重投任务把它发出去（把到期时间拨到过去，等价于时段已结束）
    event.notify_next_retry_at = datetime(2020, 1, 1)
    db.commit()
    with patch("app.services.alert_notification._post_json") as send:
        stats = retry_pending_notifications(db)
        assert send.call_count == 1, "静默时段结束后必须补发"
    db.commit()

    assert stats["due"] == 1
    assert event.notification_status == "sent"
    assert event.notify_pending_channels is None


def test_deferred_alert_not_picked_up_before_its_time(db):
    ws = _ws(db)
    _quiet_cfg(db, ws)
    event = AlertEvent(
        workspace_id=ws.id, alert_type="failed", level="error", severity="error",
        status="open", message="夜里失败了", notification_status="deferred",
        notify_pending_channels="lark",
        notify_next_retry_at=datetime(2099, 1, 1),
        dedupe_key="instance:2",
    )
    db.add(event)
    db.commit()

    with patch("app.services.alert_notification._post_json") as send:
        stats = retry_pending_notifications(db)
    assert stats["due"] == 0
    assert send.call_count == 0

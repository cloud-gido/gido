# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
值班表与静默时段。

两件事解决的是同一个问题：告警要找对人、在对的时间找。
- 值班表：告警自动落到当班人名下，卡片里点名，不再靠群里喊「这个谁看」。
- 静默时段：夜里只放过够严重的，其余压到时段结束再推——不丢告警，也不半夜叫人。
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.workspace import AlertNotificationConfig, AlertOnCallShift, User, Workspace

logger = logging.getLogger(__name__)

_UTC = ZoneInfo("UTC")
DEFAULT_TZ = "Asia/Shanghai"
# 静默时段放行阈值的默认值：只有最高级别才在夜里叫人
DEFAULT_QUIET_MIN_SEVERITY = "critical"
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def parse_hhmm(raw: Optional[str]) -> Optional[time]:
    """"09:30" → time(9, 30)。"24:00" 当作一天的末尾，用 23:59 表示。"""
    s = str(raw or "").strip()
    if not s:
        return None
    if s in ("24:00", "2400"):
        return time(23, 59, 59)
    parts = s.split(":")
    if len(parts) != 2:
        return None
    try:
        hh, mm = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return time(hh, mm)


def workspace_tz(db: Session, workspace_id: Optional[int]) -> ZoneInfo:
    if not workspace_id:
        return ZoneInfo(DEFAULT_TZ)
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    try:
        return ZoneInfo(getattr(ws, "timezone", None) or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _to_local(now_utc: datetime, tz: ZoneInfo) -> datetime:
    base = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=_UTC)
    return base.astimezone(tz)


def parse_weekdays(raw: Optional[str]) -> Optional[set[int]]:
    """"*" → None（每天）；"1,3,5" → {1, 3, 5}（ISO 星期，1=周一）。"""
    s = str(raw or "*").strip()
    if not s or s == "*":
        return None
    out: set[int] = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if 1 <= n <= 7:
            out.add(n)
    return out or None


def _window_covers(start: time, end: time, at: time) -> bool:
    """跨零点的班（如 22:00–06:00）要按两段判断。"""
    if start <= end:
        return start <= at <= end
    return at >= start or at <= end


def shift_covers(shift: AlertOnCallShift, local_now: datetime) -> bool:
    if not shift.enabled:
        return False
    start = parse_hhmm(shift.start_time) or time(0, 0)
    end = parse_hhmm(shift.end_time) or time(23, 59, 59)
    days = parse_weekdays(shift.weekdays)
    at = local_now.time()
    if days is None:
        return _window_covers(start, end, at)
    if start <= end:
        return local_now.isoweekday() in days and _window_covers(start, end, at)
    # 跨零点：凌晨那一段算在班次开始的那一天，否则周一 22:00 的班会在周一凌晨误判为在班
    if at >= start:
        return local_now.isoweekday() in days
    owner_day = (local_now - timedelta(days=1)).isoweekday()
    return owner_day in days


def resolve_on_call(
    db: Session, workspace_id: Optional[int], *, at: Optional[datetime] = None
) -> List[User]:
    """当前在班的人，按排班表顺序。没配值班表就返回空列表。"""
    if not workspace_id:
        return []
    shifts = (
        db.query(AlertOnCallShift)
        .filter(AlertOnCallShift.workspace_id == int(workspace_id))
        .order_by(AlertOnCallShift.id.asc())
        .all()
    )
    if not shifts:
        return []
    local_now = _to_local(at or datetime.utcnow(), workspace_tz(db, workspace_id))
    user_ids: List[int] = []
    for shift in shifts:
        try:
            if shift_covers(shift, local_now) and shift.user_id not in user_ids:
                user_ids.append(int(shift.user_id))
        except Exception:
            logger.debug("值班班次判定失败 shift_id=%s", shift.id, exc_info=True)
    if not user_ids:
        return []
    rows = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()}
    return [rows[uid] for uid in user_ids if uid in rows]


def on_call_label(db: Session, workspace_id: Optional[int], *, at: Optional[datetime] = None) -> str:
    users = resolve_on_call(db, workspace_id, at=at)
    if not users:
        return "未排班"
    return "、".join(u.username for u in users)


def on_call_emails(db: Session, workspace_id: Optional[int], *, at: Optional[datetime] = None) -> List[str]:
    return [u.email for u in resolve_on_call(db, workspace_id, at=at) if (u.email or "").strip()]


def in_quiet_hours(
    cfg: Optional[AlertNotificationConfig], tz: ZoneInfo, *, at: Optional[datetime] = None
) -> bool:
    if cfg is None or not getattr(cfg, "quiet_hours_enabled", False):
        return False
    start = parse_hhmm(getattr(cfg, "quiet_hours_start", None))
    end = parse_hhmm(getattr(cfg, "quiet_hours_end", None))
    if start is None or end is None or start == end:
        return False
    return _window_covers(start, end, _to_local(at or datetime.utcnow(), tz).time())


def quiet_hours_end_utc(
    cfg: AlertNotificationConfig, tz: ZoneInfo, *, at: Optional[datetime] = None
) -> Optional[datetime]:
    """静默时段结束的那一刻（UTC naive），用作压住的通知的重投时间。"""
    end = parse_hhmm(getattr(cfg, "quiet_hours_end", None))
    if end is None:
        return None
    local_now = _to_local(at or datetime.utcnow(), tz)
    candidate = local_now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(_UTC).replace(tzinfo=None)


def quiet_hours_decision(
    db: Session,
    cfg: Optional[AlertNotificationConfig],
    *,
    workspace_id: Optional[int],
    severity: str,
    at: Optional[datetime] = None,
) -> Tuple[bool, Optional[datetime]]:
    """
    返回 (是否要压住, 压到什么时候)。
    够严重的照常立刻推；其余压到静默时段结束——告警本身已经在告警中心，不会丢。
    """
    if cfg is None or not getattr(cfg, "quiet_hours_enabled", False):
        return False, None
    tz = workspace_tz(db, workspace_id)
    if not in_quiet_hours(cfg, tz, at=at):
        return False, None
    threshold = getattr(cfg, "quiet_hours_min_severity", None) or DEFAULT_QUIET_MIN_SEVERITY
    if _SEVERITY_ORDER.get(severity, 2) >= _SEVERITY_ORDER.get(threshold, 3):
        return False, None
    until = quiet_hours_end_utc(cfg, tz, at=at)
    if until is None:
        return False, None
    return True, until

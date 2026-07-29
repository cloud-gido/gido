# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Cron 工具：5 段 Linux ↔ DolphinScheduler Quartz，以及最近执行时间预览。"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from croniter import croniter

# 与 dolphin.set_schedule / DS schedule.timezoneId 对齐
DEFAULT_TZ = "Asia/Shanghai"


def linux_to_quartz_cron(cron_expr: str) -> str:
    """
    将 GIDO 存的 5 段 Linux cron（分 时 日 月 周）转为 DS Quartz 6 段（秒 分 时 日 月 周）。
    已是 6 段则原样返回。
    """
    raw = (cron_expr or "").strip()
    parts = raw.split()
    if len(parts) == 6:
        return raw
    if len(parts) != 5:
        return raw
    minute, hour, day, month, week = parts
    # Quartz：日与周不能同时为 *，其一用 ?
    if day == "*" and week == "*":
        return f"0 {minute} {hour} * {month} ?"
    if day != "*" and week == "*":
        return f"0 {minute} {hour} {day} {month} ?"
    if day == "*" and week != "*":
        return f"0 {minute} {hour} ? {month} {week}"
    return f"0 {minute} {hour} {day} {month} ?"


def normalize_linux_cron(cron_expr: str) -> str:
    return " ".join((cron_expr or "").strip().split())


def assert_linux_cron(cron_expr: Optional[str]) -> str:
    """校验 5 段 Linux cron 语义（croniter）。返回规范化字符串。"""
    raw = normalize_linux_cron(cron_expr or "")
    if not raw:
        raise ValueError("Cron 表达式不能为空")
    parts = raw.split()
    if len(parts) != 5:
        raise ValueError("Cron 须为 5 段（Linux 风格）：分 时 日 月 周，例如 0 2 * * *")
    if not croniter.is_valid(raw):
        raise ValueError(f"Cron 表达式无效：{raw}")
    return raw


def preview_next_runs(
    cron_expr: str,
    count: int = 5,
    timezone_id: str = DEFAULT_TZ,
    base: Optional[datetime] = None,
) -> Tuple[str, str, List[str]]:
    """
    预览最近若干次调度时间（Asia/Shanghai，与 DS 默认一致）。

    Returns:
        (linux_cron, quartz_cron, times as 'YYYY-MM-DD HH:MM:SS')
    """
    n = max(1, min(int(count or 5), 20))
    linux = assert_linux_cron(cron_expr)
    quartz = linux_to_quartz_cron(linux)
    try:
        tz = ZoneInfo(timezone_id or DEFAULT_TZ)
    except Exception:
        tz = ZoneInfo(DEFAULT_TZ)
    start = base.astimezone(tz) if base and base.tzinfo else (base.replace(tzinfo=tz) if base else datetime.now(tz))
    it = croniter(linux, start)
    times: List[str] = []
    for _ in range(n):
        nxt = it.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=tz)
        else:
            nxt = nxt.astimezone(tz)
        times.append(nxt.strftime("%Y-%m-%d %H:%M:%S"))
    return linux, quartz, times

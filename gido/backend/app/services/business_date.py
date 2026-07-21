# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""业务日 / 补数据 scheduleTime 规范化。

约定：
- ``business_date`` / ``bizdate`` 存 ``YYYY-MM-DD``（日历业务日）
- Dolphin ``scheduleTime`` 须为 ``yyyy-MM-dd HH:mm:ss``
- ``${yesterday}`` / ``$[yyyy-MM-dd-1]`` 相对该业务日，而非墙钟「今天」
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple


def normalize_business_date(value: Optional[str]) -> Optional[str]:
    """将任意常见日期串规范为 ``YYYY-MM-DD``；无法解析则返回 None。"""
    raw = (value or "").strip()
    if not raw:
        return None
    for piece, fmt in (
        (raw[:10], "%Y-%m-%d"),
        (raw[:19], "%Y-%m-%d %H:%M:%S"),
        (raw[:8], "%Y%m%d"),
    ):
        try:
            return datetime.strptime(piece, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return None


def schedule_time_for_dolphin(business_date: Optional[str] = None) -> str:
    """生成单点 ``scheduleTime``（``yyyy-MM-dd HH:mm:ss``）。

    业务日补 ``00:00:00``；未传则用当前墙钟时间。
    """
    bd = normalize_business_date(business_date)
    if bd:
        return f"{bd} 00:00:00"
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def complement_schedule_time_for_dolphin(business_date: str) -> str:
    """Dolphin ``COMPLEMENT_DATA`` 单日补数的 scheduleTime。

    DS 3.2 要求 ``start,end`` 形式；单日用同一天两次，例如
    ``2026-07-14 00:00:00,2026-07-14 00:00:00``。
    仅用 ``START_PROCESS`` + 单点时间时，UI「调度时间」常为空，宏退回墙钟。
    """
    t = schedule_time_for_dolphin(business_date)
    return f"{t},{t}"


def bizdate_and_yesterday(
    bizdate: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """返回 ``(bizdate, yesterday)``，yesterday = bizdate - 1 天。"""
    now = now or datetime.now()
    bd = normalize_business_date(bizdate) or now.strftime("%Y-%m-%d")
    base = datetime.strptime(bd, "%Y-%m-%d")
    return bd, (base - timedelta(days=1)).strftime("%Y-%m-%d")

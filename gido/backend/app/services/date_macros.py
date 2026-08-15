# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Dolphin 风格时间宏：$[yyyy-MM-dd-1]、$[yyyy-MM-dd-1/24] 等。

批 / 流 / 服产品层（Studio、Probe、Stream 预览、数据服务）须经
workspace_variables.substitute_script_variables 展开，禁止各入口各写一套。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

_DATE_MACRO_RE = re.compile(r"\$\[([^\]]+)\]([+-]\d+)?")
# 常见误写：把时间格式塞进 ${}。仅当花括号内容以日期格式 token 开头时才当时间宏，避免吃掉 ${bizdate}。
_CURLY_DATE_MACRO_RE = re.compile(r"\$\{((?:yyyy|MM|dd|HH|mm|ss)[^}]*)\}")
_DOLPHIN_OFFSET_RE = re.compile(
    r"^(?P<fmt>.+?)\s*(?P<sign>[+-])(?P<n>\d+)(?P<denoms>(?:/\d+)*)\s*$"
)
_DOLPHIN_OFFSET_UNITS = {
    "": "days",
    "/24": "hours",
    "/24/60": "minutes",
    "/24/60/60": "seconds",
}


def expand_date_macros_in_text(
    text: str,
    *,
    bizdate: Optional[str] = None,
    tz_name: str = "Asia/Shanghai",
) -> str:
    """替换文本中所有 ``$[yyyy-MM-dd-1/24]`` 类占位符。"""
    if not text:
        return text

    def _sq(m: re.Match[str]) -> str:
        return resolve_date_expr(f"$[{m.group(1)}{m.group(2) or ''}]", bizdate, tz_name)

    def _curly(m: re.Match[str]) -> str:
        return resolve_date_expr(f"$[{m.group(1)}]", bizdate, tz_name)

    out = _DATE_MACRO_RE.sub(_sq, text)
    return _CURLY_DATE_MACRO_RE.sub(_curly, out)


def resolve_date_expr(
    expr: str,
    bizdate: Optional[str] = None,
    tz_name: str = "Asia/Shanghai",
) -> str:
    """
    对齐 DolphinScheduler 时间占位符。
    $[yyyy-MM-dd] / $[yyyy-MM-dd-1] / $[yyyy-MM-dd-1/24] / $[yyyy-MM-dd-1/24/60]
    """
    try:
        import pytz

        tz = pytz.timezone(tz_name or "Asia/Shanghai")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()

    m = re.fullmatch(r"\$\[(.+)\]", (expr or "").strip())
    if not m:
        return expr

    inner = m.group(1).strip()
    delta = timedelta(0)
    om = _DOLPHIN_OFFSET_RE.match(inner)
    if om and (om.group("denoms") or "") in _DOLPHIN_OFFSET_UNITS:
        n = int(om.group("n"))
        if om.group("sign") == "-":
            n = -n
        inner = om.group("fmt").rstrip()
        unit = _DOLPHIN_OFFSET_UNITS[om.group("denoms") or ""]
        delta = timedelta(**{unit: n})

    if bizdate:
        try:
            from app.services.business_date import normalize_business_date

            bd = normalize_business_date(bizdate)
            base_date = datetime.strptime(bd or "", "%Y-%m-%d")
        except Exception:
            try:
                base_date = datetime.strptime(str(bizdate)[:10], "%Y-%m-%d")
            except ValueError:
                base_date = now.replace(tzinfo=None)
    else:
        base_date = now.replace(tzinfo=None)

    has_time = any(c in inner for c in ("H", "m", "s"))
    if has_time:
        base_date = base_date.replace(hour=now.hour, minute=now.minute, second=now.second)
    target = base_date + delta

    fmt = inner.replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d")
    fmt = fmt.replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
    try:
        return target.strftime(fmt)
    except Exception:
        return expr

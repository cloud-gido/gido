# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""与 Studio SQL / workspace_variables 对齐的时间宏与 ${var} 替换。

解析失败时保留原文，不抛异常（避免 PYTHON 节点体感差异或误杀脚本）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, Optional

_DATE_MACRO_RE = re.compile(r"\$\[([^\]]+)\]([+-]\d+)?")
_CURLY_DATE_MACRO_RE = re.compile(r"\$\{((?:yyyy|MM|dd|HH|mm|ss)[^}]*)\}")
# Dolphin：格式后可接 ±N（天）±N/24（时）±N/24/60（分）±N/24/60/60（秒）
_DOLPHIN_OFFSET_RE = re.compile(
    r"^(?P<fmt>.+?)\s*(?P<sign>[+-])(?P<n>\d+)(?P<denoms>(?:/\d+)*)\s*$"
)
_DOLPHIN_OFFSET_UNITS = {
    "": "days",
    "/24": "hours",
    "/24/60": "minutes",
    "/24/60/60": "seconds",
}


def split_dolphin_time_offset(inner: str) -> tuple[str, timedelta]:
    """拆出日期格式与 Dolphin 风格偏移；无法识别则偏移为 0、格式保持原文。"""
    raw = (inner or "").strip()
    m = _DOLPHIN_OFFSET_RE.match(raw)
    if not m:
        return raw, timedelta(0)
    unit = _DOLPHIN_OFFSET_UNITS.get(m.group("denoms") or "")
    if not unit:
        return raw, timedelta(0)
    n = int(m.group("n"))
    if m.group("sign") == "-":
        n = -n
    return m.group("fmt").rstrip(), timedelta(**{unit: n})


def resolve_date_expr(
    expr: str,
    bizdate: Optional[str] = None,
    tz_name: str = "Asia/Shanghai",
) -> str:
    """对齐 app.api.studio._resolve_date_expr。"""
    try:
        import pytz

        tz = pytz.timezone(tz_name or "Asia/Shanghai")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()

    m = re.fullmatch(r"\$\[(.+)\]", (expr or "").strip())
    if not m:
        return expr

    inner, delta = split_dolphin_time_offset(m.group(1).strip())

    if bizdate:
        try:
            base_date = datetime.strptime(bizdate, "%Y-%m-%d")
        except ValueError:
            base_date = now.replace(tzinfo=None)
    else:
        base_date = now.replace(tzinfo=None)

    has_time = any(c in inner for c in ("H", "m", "s"))
    if has_time:
        base_date = base_date.replace(hour=now.hour, minute=now.minute, second=now.second)
    target = base_date + delta

    fmt = inner
    fmt = fmt.replace("yyyy", "%Y").replace("MM", "%m").replace("dd", "%d")
    fmt = fmt.replace("HH", "%H").replace("mm", "%M").replace("ss", "%S")
    try:
        return target.strftime(fmt)
    except Exception:
        return expr


def substitute_sql_macros(
    sql: str,
    *,
    bizdate: Optional[str] = None,
    tz_name: str = "Asia/Shanghai",
    variables: Optional[Mapping[str, Any]] = None,
) -> str:
    """替换 ${bizdate}/${yesterday}/${key} 与 $[...]，与 studio_sql_run 一致。"""
    if not sql:
        return sql
    try:
        try:
            import pytz

            now_local = datetime.now(pytz.timezone(tz_name or "Asia/Shanghai"))
        except Exception:
            now_local = datetime.now()

        # yesterday 相对 bizdate，而非墙钟「今天」
        raw_biz = (bizdate or "").strip()
        try:
            base = datetime.strptime(raw_biz, "%Y-%m-%d") if raw_biz else now_local.replace(tzinfo=None)
            biz = base.strftime("%Y-%m-%d")
        except ValueError:
            base = now_local.replace(tzinfo=None)
            biz = base.strftime("%Y-%m-%d")
        yesterday_str = (base - timedelta(days=1)).strftime("%Y-%m-%d")
        text = sql.replace("${bizdate}", biz).replace("${yesterday}", yesterday_str)
        macro_biz = raw_biz or None

        def _expand(s: str) -> str:
            out = _DATE_MACRO_RE.sub(
                lambda m: resolve_date_expr(f"$[{m.group(1)}{m.group(2) or ''}]", macro_biz, tz_name),
                s,
            )
            return _CURLY_DATE_MACRO_RE.sub(
                lambda m: resolve_date_expr(f"$[{m.group(1)}]", macro_biz, tz_name),
                out,
            )

        for key, raw in (variables or {}).items():
            k = str(key).strip()
            if not k:
                continue
            text = text.replace(f"${{{k}}}", _expand("" if raw is None else str(raw)))

        return _expand(text)
    except Exception:
        # 任何异常都不阻断 execute：退回原文
        return sql

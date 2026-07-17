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

    inner = m.group(1).strip()
    offset_days = 0
    offset_match = re.search(r"([+-]\d+)$", inner)
    if offset_match:
        try:
            offset_days = int(offset_match.group(1))
        except ValueError:
            return expr
        # 兼容 $[yyyy-MM-dd -3]（offset 前有空格）
        inner = inner[: offset_match.start()].rstrip()

    if bizdate:
        try:
            base_date = datetime.strptime(bizdate, "%Y-%m-%d")
        except ValueError:
            base_date = now.replace(tzinfo=None)
    else:
        base_date = now.replace(tzinfo=None)

    target = base_date + timedelta(days=offset_days)
    has_time = any(c in inner for c in ("H", "m", "s"))
    if has_time:
        target = target.replace(hour=now.hour, minute=now.minute, second=now.second)

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

        biz = (bizdate or "").strip() or now_local.strftime("%Y-%m-%d")
        yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
        text = sql.replace("${bizdate}", biz).replace("${yesterday}", yesterday_str)

        for key, raw in (variables or {}).items():
            k = str(key).strip()
            if not k:
                continue
            val = _DATE_MACRO_RE.sub(
                lambda m: resolve_date_expr(f"$[{m.group(1)}{m.group(2) or ''}]", biz, tz_name),
                "" if raw is None else str(raw),
            )
            text = text.replace(f"${{{k}}}", val)

        text = _DATE_MACRO_RE.sub(
            lambda m: resolve_date_expr(f"$[{m.group(1)}{m.group(2) or ''}]", biz, tz_name),
            text,
        )
        return text
    except Exception:
        # 任何异常都不阻断 execute：退回原文
        return sql

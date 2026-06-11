# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作空间全局变量：Batch / Stream / Serve 共用 ${key} 与 $[...] 时间宏替换。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.models.workspace import Workspace, WorkspaceVariable

VALID_SCOPES = frozenset({"all", "batch", "stream", "serve"})


def load_workspace_variable_map(
    db: Session,
    workspace_id: int,
    scope: str,
) -> Dict[str, str]:
    """加载 scope 与 all 的变量；后写覆盖先写（all 先，具体 scope 后）。"""
    want = (scope or "all").strip().lower()
    rows = (
        db.query(WorkspaceVariable)
        .filter(WorkspaceVariable.workspace_id == int(workspace_id))
        .filter(WorkspaceVariable.scope.in_(["all", want]))
        .order_by(WorkspaceVariable.id.asc())
        .all()
    )
    out: Dict[str, str] = {}
    for row in rows:
        key = (row.var_key or "").strip()
        if key:
            out[key] = row.var_value if row.var_value is not None else ""
    return out


def substitute_script_variables(
    db: Session,
    workspace_id: int,
    script: str,
    scope: str,
    *,
    bizdate: Optional[str] = None,
    extra_vars: Optional[Dict[str, str]] = None,
) -> str:
    """将工作空间变量、节点/作业级 extra_vars、时间宏写入脚本（不修改库中原文）。"""
    if not script:
        return script

    ws = db.query(Workspace).filter(Workspace.id == int(workspace_id)).first()
    tz_name = (ws.timezone if ws else None) or "Asia/Shanghai"

    try:
        import pytz

        now_local = datetime.now(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.now()

    biz = bizdate or now_local.strftime("%Y-%m-%d")
    yesterday_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
    text = script.replace("${bizdate}", biz).replace("${yesterday}", yesterday_str)

    merged = load_workspace_variable_map(db, workspace_id, scope)
    if extra_vars:
        for k, v in extra_vars.items():
            merged[str(k)] = "" if v is None else str(v)

    from app.api.studio import _resolve_date_expr

    for key, raw in merged.items():
        val = re.sub(
            r"\$\[([^\]]+)\]([+-]\d+)?",
            lambda m: _resolve_date_expr(f"$[{m.group(1)}{m.group(2) or ''}]", biz, tz_name),
            str(raw),
        )
        text = text.replace(f"${{{key}}}", val)

    text = re.sub(
        r"\$\[([^\]]+)\]([+-]\d+)?",
        lambda m: _resolve_date_expr(f"$[{m.group(1)}{m.group(2) or ''}]", biz, tz_name),
        text,
    )
    return text


def mask_secret_value(value: Optional[str]) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) <= 4:
        return "****"
    return s[:2] + "****" + s[-2:]

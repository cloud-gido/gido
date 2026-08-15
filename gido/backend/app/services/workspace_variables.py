# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作空间全局变量：Batch / Stream / Serve 共用 ${key} 与 $[...] 时间宏替换。"""
from __future__ import annotations

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
    """产品层脚本展开：空间变量 ``${key}`` + 时间宏 ``$[yyyy-MM-dd-1/24]``。

    批 / 流 / 服跑 SQL（或同类脚本）必须走这里，体验一致：
    - batch：Studio SQL/PYTHON/SHELL、Probe、工作流节点试跑、Copilot 只读查询
    - stream：Stream SQL 预览、提交前 SQL
    - serve：数据服务 API 执行 / 测试
    """
    if not script:
        return script

    ws = db.query(Workspace).filter(Workspace.id == int(workspace_id)).first()
    tz_name = (ws.timezone if ws else None) or "Asia/Shanghai"

    try:
        import pytz

        now_local = datetime.now(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.now()

    from app.services.business_date import bizdate_and_yesterday, normalize_business_date

    biz, yesterday_str = bizdate_and_yesterday(
        normalize_business_date(bizdate),
        now=now_local.replace(tzinfo=None),
    )
    text = script.replace("${bizdate}", biz).replace("${yesterday}", yesterday_str)

    merged = load_workspace_variable_map(db, workspace_id, scope)
    if extra_vars:
        for k, v in extra_vars.items():
            merged[str(k)] = "" if v is None else str(v)

    from app.services.date_macros import expand_date_macros_in_text

    for key, raw in merged.items():
        val = expand_date_macros_in_text(str(raw), bizdate=biz, tz_name=tz_name)
        text = text.replace(f"${{{key}}}", val)

    return expand_date_macros_in_text(text, bizdate=biz, tz_name=tz_name)


def mask_secret_value(value: Optional[str]) -> str:
    if not value:
        return ""
    s = str(value)
    if len(s) <= 4:
        return "****"
    return s[:2] + "****" + s[-2:]

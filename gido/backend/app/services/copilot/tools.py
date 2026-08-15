# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot 工具：封装数据字典与只读探查。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core import perm_codes as PC
from app.core.config import settings
from app.models.workspace import DataSource, MetaColumn, MetaTable, User
from app.services.rbac import assert_workspace_data_capability, require_datasource_row, require_meta_table
from app.services.sql_readonly import parse_readonly_statements
from app.api.probe import _execute_one

_PROBE_DS_TYPES = frozenset({"mysql", "doris", "postgresql"})

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "列出工作空间内已注册的数据表，可按关键词过滤表名或注释",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "表名或注释关键词，可选"},
                    "limit": {"type": "integer", "description": "返回条数上限，默认 30", "minimum": 1, "maximum": 100},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "获取已注册元数据表的字段结构",
            "parameters": {
                "type": "object",
                "properties": {
                    "meta_table_id": {"type": "integer", "description": "元数据表 ID（来自 list_tables）"},
                },
                "required": ["meta_table_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_readonly_sql",
            "description": "在工作空间数据源上执行只读 SQL（SELECT 或 WITH），用于数据探查",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "只读 SQL 语句"},
                    "limit": {"type": "integer", "description": "结果行数上限，默认 500", "minimum": 1, "maximum": 10000},
                },
                "required": ["sql"],
            },
        },
    },
]


def _list_tables(
    db: Session,
    user: User,
    workspace_id: int,
    keyword: Optional[str] = None,
    limit: int = 30,
) -> Dict[str, Any]:
    assert_workspace_data_capability(db, user, workspace_id, "viewer", PC.GIDO_BATCH_DATAMAP_READ)
    q = db.query(MetaTable).filter(MetaTable.workspace_id == workspace_id)
    if keyword:
        kw = keyword.strip()
        q = q.filter(MetaTable.table_name.contains(kw) | MetaTable.table_comment.contains(kw))
    tables = q.limit(min(max(limit, 1), 100)).all()
    rows = []
    for t in tables:
        ds = db.query(DataSource).filter(DataSource.id == t.datasource_id).first()
        rows.append({
            "meta_table_id": t.id,
            "table_name": t.table_name,
            "db_name": t.db_name,
            "table_comment": t.table_comment or "",
            "datasource_id": t.datasource_id,
            "datasource_name": ds.name if ds else None,
            "ds_type": ds.ds_type if ds else None,
        })
    return {"count": len(rows), "tables": rows}


def _describe_table(db: Session, user: User, meta_table_id: int) -> Dict[str, Any]:
    table = require_meta_table(db, user, meta_table_id)
    columns = (
        db.query(MetaColumn)
        .filter(MetaColumn.table_id == meta_table_id)
        .order_by(MetaColumn.ordinal_position)
        .all()
    )
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first()
    return {
        "meta_table_id": table.id,
        "table_name": table.table_name,
        "db_name": table.db_name,
        "table_comment": table.table_comment or "",
        "datasource_id": table.datasource_id,
        "datasource_name": ds.name if ds else None,
        "ds_type": ds.ds_type if ds else None,
        "columns": [
            {
                "name": c.column_name,
                "type": c.column_type,
                "comment": c.column_comment or "",
                "nullable": c.is_nullable,
                "primary_key": c.is_primary_key,
            }
            for c in columns
        ],
    }


def _run_readonly_sql(
    db: Session,
    user: User,
    workspace_id: int,
    datasource_id: int,
    sql: str,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    assert_workspace_data_capability(db, user, workspace_id, "viewer", PC.GIDO_BATCH_PROBE_READ)
    ds = require_datasource_row(db, user, datasource_id)
    if ds.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="数据源不属于该工作空间")
    from app.services.workspace_variables import substitute_script_variables

    sql = substitute_script_variables(db, workspace_id, sql or "", "batch")
    lt = (ds.ds_type or "").lower()
    if lt not in _PROBE_DS_TYPES:
        raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型的探查: {ds.ds_type}")

    statements = parse_readonly_statements(sql)
    lim = min(max(limit or settings.COPILOT_PROBE_DEFAULT_LIMIT, 1), 10000)
    stmt = statements[-1] if statements else sql
    block = _execute_one(ds, stmt, lim)
    return {
        "sql": stmt,
        "columns": block.get("columns") or [],
        "column_types": block.get("column_types") or [],
        "rows": block.get("rows") or [],
        "total": block.get("total", 0),
        "truncated": bool(block.get("truncated")),
        "datasource_id": datasource_id,
        "datasource_name": ds.name,
    }


def run_tool(
    db: Session,
    user: User,
    workspace_id: int,
    datasource_id: Optional[int],
    name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    if name == "list_tables":
        return _list_tables(
            db,
            user,
            workspace_id,
            keyword=arguments.get("keyword"),
            limit=int(arguments.get("limit") or 30),
        )
    if name == "describe_table":
        mid = arguments.get("meta_table_id")
        if mid is None:
            return {"error": "缺少 meta_table_id"}
        return _describe_table(db, user, int(mid))
    if name == "run_readonly_sql":
        if not datasource_id:
            return {"error": "请在前端选择数据源后再执行 SQL 查询"}
        return _run_readonly_sql(
            db,
            user,
            workspace_id,
            datasource_id,
            str(arguments.get("sql") or ""),
            limit=arguments.get("limit"),
        )
    return {"error": f"未知工具: {name}"}


def tool_result_for_llm(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """传给 LLM 的精简结果（不含完整行数据）。"""
    if name == "run_readonly_sql":
        if raw.get("error"):
            return raw
        rows = raw.get("rows") or []
        preview = rows[:3]
        return {
            "sql": raw.get("sql"),
            "columns": raw.get("columns"),
            "row_count": len(rows),
            "total": raw.get("total"),
            "truncated": raw.get("truncated"),
            "preview_rows": preview,
            "note": "完整结果已展示给用户，请根据 preview 与 row_count 总结",
        }
    if name == "list_tables":
        return raw
    if name == "describe_table":
        return raw
    return raw

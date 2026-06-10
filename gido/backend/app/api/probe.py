# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据探查：临时只读 SQL（SELECT / WITH），支持多条语句。"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import DataSource, User
from app.services.rbac import assert_workspace_data_capability, require_datasource_row
from app.services.datasource_mysql_user import mysql_protocol_connect_user
from app.services.sql_readonly import parse_readonly_statements, result_set_from_cursor

router = APIRouter(prefix="/probe", tags=["数据探查"])


class ProbeQueryIn(BaseModel):
    workspace_id: int
    datasource_id: int
    sql: str
    limit: int = Field(default=500, ge=1, le=10000)


def _execute_one(ds: DataSource, stmt: str, lim: int) -> Dict[str, Any]:
    lt = (ds.ds_type or "").lower()
    if lt in ("mysql", "doris"):
        import pymysql

        conn = pymysql.connect(
            host=ds.host,
            port=ds.port or 3306,
            user=mysql_protocol_connect_user(ds),
            password=ds.password or "",
            database=(ds.database or ""),
            connect_timeout=12,
        )
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM ({stmt}) AS _dw_probe_sub LIMIT %s", (lim,))
            rows = cur.fetchall()
            base = result_set_from_cursor(lt, cur.description, rows, lim)
            base["sql"] = stmt
            return base
        finally:
            conn.close()

    if lt == "postgresql":
        import psycopg2

        dbname = (ds.database or "").strip()
        if not dbname:
            raise HTTPException(status_code=400, detail="PostgreSQL 数据源未配置数据库名")
        conn = psycopg2.connect(
            host=ds.host or "127.0.0.1",
            port=ds.port or 5432,
            user=(ds.username or "").strip() or None,
            password=ds.password or "",
            dbname=dbname,
            connect_timeout=12,
        )
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM (" + stmt + ") AS _dw_probe_sub LIMIT %s", (lim,))
            rows = cur.fetchall()
            base = result_set_from_cursor(lt, cur.description, rows, lim)
            base["sql"] = stmt
            return base
        finally:
            conn.close()

    raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型的探查: {ds.ds_type}")


@router.post("/query")
def probe_query(
    body: ProbeQueryIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "viewer", PC.GIDO_BATCH_PROBE_READ)
    ds = require_datasource_row(db, current_user, body.datasource_id)
    if ds.workspace_id != body.workspace_id:
        raise HTTPException(status_code=400, detail="数据源不属于该工作空间")
    statements = parse_readonly_statements(body.sql)
    lim = min(max(body.limit, 1), 10000)

    results: List[Dict[str, Any]] = []
    errors: List[Optional[str]] = []
    for idx, stmt in enumerate(statements):
        try:
            block = _execute_one(ds, stmt, lim)
            block["index"] = idx
            block["error"] = None
            results.append(block)
            errors.append(None)
        except HTTPException:
            raise
        except Exception as e:
            results.append(
                {
                    "index": idx,
                    "sql": stmt,
                    "columns": [],
                    "column_types": [],
                    "rows": [],
                    "total": 0,
                    "truncated": False,
                    "error": str(e)[:2000],
                }
            )
            errors.append(str(e)[:2000])

    last_ok = next((r for r in reversed(results) if not r.get("error")), results[-1] if results else None)
    return {
        "statement_count": len(statements),
        "statements": results,
        "columns": last_ok.get("columns") if last_ok else [],
        "column_types": last_ok.get("column_types") if last_ok else [],
        "rows": last_ok.get("rows") if last_ok else [],
        "total": last_ok.get("total", 0) if last_ok else 0,
        "truncated": bool(last_ok.get("truncated")) if last_ok else False,
        "has_errors": any(errors),
    }

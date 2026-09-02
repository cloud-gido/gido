# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据探查：临时只读 SQL（SELECT / WITH），支持多条语句。"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import DataSource, ProbeQueryTree, User, Workspace
from app.services.rbac import assert_workspace_data_capability, require_datasource_row
from app.services.datasource_mysql_user import mysql_protocol_connect_user
from app.services.sql_readonly import apply_readonly_row_limit, parse_readonly_statements, result_set_from_cursor
from app.services.probe_tree_store import sanitize_probe_tree_state

router = APIRouter(prefix="/probe", tags=["数据探查"])


def shared_probe_tree_user_id(db: Session, workspace_id: int, current_user: User) -> int:
    """
    让 Probe 侧目录树在同一 workspace 内对所有人可见/可保存。

    由于历史表结构 `uq_probe_tree_ws_user` 以 (workspace_id, user_id) 唯一，
    这里复用 workspace.owner_id 作为“共享 owner”。
    """
    try:
        ws = db.query(Workspace).filter(Workspace.id == int(workspace_id)).first()
        if ws and getattr(ws, "owner_id", None):
            return int(ws.owner_id)
    except Exception:
        pass
    return int(current_user.id)


class ProbeQueryIn(BaseModel):
    workspace_id: int
    datasource_id: int
    sql: str
    limit: int = Field(default=10000, ge=1, le=10000)


class ProbeTreeIn(BaseModel):
    workspace_id: int
    folders: List[dict] = Field(default_factory=list)
    scripts: List[dict] = Field(default_factory=list)
    activeScriptId: Optional[str] = None


@router.get("/tree")
def get_probe_tree(
    workspace_id: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户在该空间的探查目录树。"""
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_BATCH_PROBE_READ)
    share_uid = shared_probe_tree_user_id(db, workspace_id, current_user)
    row = (
        db.query(ProbeQueryTree)
        .filter(
            ProbeQueryTree.workspace_id == workspace_id,
            ProbeQueryTree.user_id == share_uid,
        )
        .first()
    )
    if not row or not row.state:
        return {"folders": [], "scripts": [], "activeScriptId": None}
    return row.state


@router.put("/tree")
def put_probe_tree(
    body: ProbeTreeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "viewer", PC.GIDO_BATCH_PROBE_READ)
    try:
        state = sanitize_probe_tree_state(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    share_uid = shared_probe_tree_user_id(db, body.workspace_id, current_user)
    row = (
        db.query(ProbeQueryTree)
        .filter(
            ProbeQueryTree.workspace_id == body.workspace_id,
            ProbeQueryTree.user_id == share_uid,
        )
        .first()
    )
    if row:
        row.state = state
        row.updated_at = datetime.utcnow()
    else:
        row = ProbeQueryTree(
            workspace_id=body.workspace_id,
            user_id=share_uid,
            state=state,
            updated_at=datetime.utcnow(),
        )
        db.add(row)
    db.commit()
    return state


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
            cur.execute(apply_readonly_row_limit(stmt, lim))
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
            cur.execute(apply_readonly_row_limit(stmt, lim))
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
    from app.services.workspace_variables import substitute_script_variables

    sql = substitute_script_variables(db, body.workspace_id, body.sql or "", "batch")
    statements = parse_readonly_statements(sql)
    lim = min(max(body.limit, 1), 10000)

    from datetime import datetime

    started_at = datetime.utcnow()
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
    finished_at = datetime.utcnow()
    has_errors = any(errors)
    has_success = any(r for r in results if not r.get("error"))
    status = "success" if has_success else "failed"

    payload = {
        "statement_count": len(statements),
        "statements": results,
        "columns": last_ok.get("columns") if last_ok else [],
        "column_types": last_ok.get("column_types") if last_ok else [],
        "rows": last_ok.get("rows") if last_ok else [],
        "total": last_ok.get("total", 0) if last_ok else 0,
        "truncated": bool(last_ok.get("truncated")) if last_ok else False,
        "has_errors": has_errors,
    }

    try:
        from app.services.adhoc_run_store import save_adhoc_run

        err_msg = next((e for e in reversed(errors) if e), None)
        result_preview = None
        if last_ok and not last_ok.get("error"):
            result_preview = {
                "columns": last_ok.get("columns") or [],
                "column_types": last_ok.get("column_types") or [],
                "rows": last_ok.get("rows") or [],
                "total": last_ok.get("total", 0),
                "truncated": bool(last_ok.get("truncated")),
            }
        save_adhoc_run(
            db,
            workspace_id=body.workspace_id,
            source="probe",
            triggered_by=current_user.id,
            status=status,
            sql_text=body.sql,
            datasource_id=body.datasource_id,
            object_name=ds.name,
            error_message=err_msg,
            log_content=None,
            result=result_preview,
            started_at=started_at,
            finished_at=finished_at,
        )
    except Exception:
        pass

    return payload

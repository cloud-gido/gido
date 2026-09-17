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
from app.core.config import settings
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
    client_key: Optional[str] = Field(default=None, max_length=128)


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
        # 自动保存会并发打进来（多标签页、连续保存），首次保存时两个请求都会查到「没有」。
        # (workspace_id, user_id) 上有唯一约束，先查再插会让其中一个 500。
        from app.services.db_idempotent import insert_or_get

        row, created = insert_or_get(
            db,
            ProbeQueryTree(
                workspace_id=body.workspace_id,
                user_id=share_uid,
                state=state,
                updated_at=datetime.utcnow(),
            ),
            lambda: (
                db.query(ProbeQueryTree)
                .filter(
                    ProbeQueryTree.workspace_id == body.workspace_id,
                    ProbeQueryTree.user_id == share_uid,
                )
                .first()
            ),
        )
        if not created:
            row.state = state
            row.updated_at = datetime.utcnow()
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


@router.post("/runs", status_code=202)
def submit_probe_run_async(
    body: ProbeQueryIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交持久化只读探查，立即返回 run_id。"""
    from app.services.adhoc_run_worker import submit_probe_run

    if not settings.ADHOC_ASYNC_ENABLED:
        raise HTTPException(status_code=503, detail="异步交互式运行尚未启用")
    assert_workspace_data_capability(
        db, current_user, body.workspace_id, "viewer", PC.GIDO_BATCH_PROBE_READ
    )
    ds = require_datasource_row(
        db, current_user, body.datasource_id, PC.GIDO_BATCH_PROBE_READ
    )
    if ds.workspace_id != body.workspace_id:
        raise HTTPException(status_code=400, detail="数据源不属于该工作空间")
    # 提交前完成只读校验，非法 SQL 不进入后台队列。
    parse_readonly_statements(body.sql or "")
    row, reused = submit_probe_run(
        db,
        workspace_id=body.workspace_id,
        datasource_id=body.datasource_id,
        user_id=current_user.id,
        sql=body.sql,
        limit=body.limit,
        object_name=f"probe:{body.client_key}" if body.client_key else ds.name,
    )
    return {"run_id": row.id, "status": row.status, "reused": reused}

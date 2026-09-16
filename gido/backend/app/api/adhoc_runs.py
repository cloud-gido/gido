# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""运行历史：数据开发试跑与数据探查交互式执行记录。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core import perm_codes as PC
from app.core.access import user_has_any
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import AdhocRun, AdhocRunLogChunk, DataSource, User
from app.services.adhoc_run_store import serialize_adhoc_run
from app.services.rbac import (
    assert_workspace_access,
    get_user_role,
    workspace_data_full_control,
)

router = APIRouter(prefix="/adhoc-runs", tags=["运行历史"])


def _allowed_sources(db: Session, user: User, workspace_id: int) -> list[str]:
    """按权限决定可见来源；空间全权可见全部。"""
    if workspace_data_full_control(db, user, workspace_id):
        return ["studio", "probe"]
    role = get_user_role(db, user, workspace_id)
    out: list[str] = []
    if role in ("developer", "admin") or user_has_any(db, user, [PC.GIDO_BATCH_STUDIO_READ]):
        out.append("studio")
    if role in ("viewer", "developer", "admin") or user_has_any(db, user, [PC.GIDO_BATCH_PROBE_READ]):
        out.append("probe")
    return out


def _assert_can_view_run(db: Session, user: User, row: AdhocRun, *, allow_others_if_admin: bool = True) -> None:
    assert_workspace_access(db, user, row.workspace_id)
    allowed = _allowed_sources(db, user, row.workspace_id)
    if row.source not in allowed:
        raise HTTPException(status_code=403, detail="无权查看该来源的运行记录")
    if allow_others_if_admin and workspace_data_full_control(db, user, row.workspace_id):
        return
    if row.triggered_by != user.id:
        raise HTTPException(status_code=403, detail="仅可查看本人的运行记录")


@router.get("")
def list_adhoc_runs(
    workspace_id: int,
    source: Optional[str] = Query(None, description="studio | probe"),
    status: Optional[str] = None,
    mine_only: bool = Query(True, description="默认仅本人；空间管理员可设为 false"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_access(db, current_user, workspace_id)
    allowed = _allowed_sources(db, current_user, workspace_id)
    if not allowed:
        raise HTTPException(status_code=403, detail="无权查看运行历史")

    q = db.query(AdhocRun).filter(AdhocRun.workspace_id == workspace_id, AdhocRun.source.in_(allowed))
    if source:
        src = source.strip().lower()
        if src not in allowed:
            raise HTTPException(status_code=403, detail=f"无权查看来源 {src}")
        q = q.filter(AdhocRun.source == src)
    if status:
        q = q.filter(AdhocRun.status == status)

    is_admin = workspace_data_full_control(db, current_user, workspace_id)
    if mine_only or not is_admin:
        q = q.filter(AdhocRun.triggered_by == current_user.id)

    total = q.count()
    rows = (
        q.order_by(desc(AdhocRun.created_at), desc(AdhocRun.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    user_ids = {r.triggered_by for r in rows if r.triggered_by}
    users = {
        u.id: u
        for u in db.query(User).filter(User.id.in_(user_ids)).all()
    } if user_ids else {}
    ds_ids = {r.datasource_id for r in rows if r.datasource_id}
    datasources = {
        d.id: d
        for d in db.query(DataSource).filter(DataSource.id.in_(ds_ids)).all()
    } if ds_ids else {}

    items = []
    for r in rows:
        item = serialize_adhoc_run(r, include_result=False, include_sql=False)
        u = users.get(r.triggered_by) if r.triggered_by else None
        item["triggered_by_name"] = (u.full_name or u.username) if u else None
        ds = datasources.get(r.datasource_id) if r.datasource_id else None
        item["datasource_name"] = ds.name if ds else None
        items.append(item)

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "allowed_sources": allowed,
        "can_view_all": is_admin,
    }


@router.get("/active")
def get_active_adhoc_run(
    workspace_id: int,
    node_id: Optional[int] = None,
    source: str = "studio",
    object_name: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """恢复当前用户在节点/入口上的活动运行。"""
    assert_workspace_access(db, current_user, workspace_id)
    allowed = _allowed_sources(db, current_user, workspace_id)
    src = source.strip().lower()
    if src not in allowed:
        raise HTTPException(status_code=403, detail=f"无权查看来源 {src}")
    q = db.query(AdhocRun).filter(
        AdhocRun.workspace_id == workspace_id,
        AdhocRun.source == src,
        AdhocRun.triggered_by == current_user.id,
        AdhocRun.status.in_(["queued", "running", "cancel_requested"]),
    )
    if node_id is not None:
        q = q.filter(AdhocRun.node_id == node_id)
    if object_name:
        q = q.filter(AdhocRun.object_name == object_name)
    row = q.order_by(desc(AdhocRun.id)).first()
    return serialize_adhoc_run(row, include_result=True) if row else None


@router.get("/{run_id}")
def get_adhoc_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)

    item = serialize_adhoc_run(row, include_result=True)
    u = db.query(User).filter(User.id == row.triggered_by).first() if row.triggered_by else None
    item["triggered_by_name"] = (u.full_name or u.username) if u else None
    ds = db.query(DataSource).filter(DataSource.id == row.datasource_id).first() if row.datasource_id else None
    item["datasource_name"] = ds.name if ds else None
    return item


@router.get("/{run_id}/logs")
def get_adhoc_run_logs(
    run_id: int,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)
    chunks = (
        db.query(AdhocRunLogChunk)
        .filter(
            AdhocRunLogChunk.run_id == run_id,
            AdhocRunLogChunk.seq > after_seq,
        )
        .order_by(AdhocRunLogChunk.seq)
        .limit(limit)
        .all()
    )
    next_seq = chunks[-1].seq if chunks else after_seq
    return {
        "run_id": run_id,
        "status": row.status,
        "chunks": [
            {
                "seq": item.seq,
                "stream": item.stream,
                "content": item.content,
                "created_at": item.created_at,
            }
            for item in chunks
        ],
        "next_seq": next_seq,
        "has_more": len(chunks) >= limit,
    }


@router.post("/{run_id}/cancel")
def cancel_adhoc_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.adhoc_run_worker import request_cancel

    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)
    if row.triggered_by != current_user.id and not workspace_data_full_control(
        db, current_user, row.workspace_id
    ):
        raise HTTPException(status_code=403, detail="仅执行人或空间管理员可停止运行")
    row = request_cancel(db, row)
    return {"run_id": row.id, "status": row.status}

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
from app.models.workspace import AdhocRun, DataSource, User
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
        item = serialize_adhoc_run(r, include_result=False)
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

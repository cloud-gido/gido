# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""运行历史：数据开发试跑与数据探查交互式执行记录。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import perm_codes as PC
from app.core.access import user_has_any
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import (
    AdhocRun,
    AdhocRunExport,
    AdhocRunLogChunk,
    AdhocRunShare,
    AdhocRunShareGrant,
    AdhocRunStatement,
    DataSource,
    User,
)
from app.services.adhoc_run_store import (
    list_run_statements,
    paginate_statement_rows,
    paginate_statement_result_chunks,
    query_statement_rows,
    serialize_adhoc_run,
    serialize_run_statement,
    statement_collection_version,
)
from app.services.rbac import (
    assert_workspace_access,
    get_user_role,
    workspace_data_full_control,
)

router = APIRouter(prefix="/adhoc-runs", tags=["运行历史"])
ACTIVE_RUN_STATUSES = ("queued", "running", "cancel_requested")


class AdhocExportCreate(BaseModel):
    format: str
    statement_index: Optional[int] = None
    search: Optional[str] = None
    filters: List["AdhocRowFilter"] = Field(default_factory=list)
    sort: List["AdhocRowSort"] = Field(default_factory=list)
    statement_version: Optional[str] = None


class AdhocShareCreate(BaseModel):
    ttl_hours: Optional[int] = Field(None, ge=1, le=24 * 30)


class AdhocRowFilter(BaseModel):
    column: str
    operator: str
    value: Any = None


class AdhocRowSort(BaseModel):
    column: str
    direction: str


AdhocExportCreate.model_rebuild()


class AdhocRowQuery(BaseModel):
    search: Optional[str] = None
    filters: List[AdhocRowFilter] = Field(default_factory=list)
    sort: List[AdhocRowSort] = Field(default_factory=list)
    cursor: Optional[str] = None
    limit: int = Field(500, ge=1, le=5000)
    # Optional: omit or send a stale version for the first page; the server soft-upgrades
    # while rows are still materializing. Cursor pages still require a matching version.
    statement_version: Optional[str] = None


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
    if row.triggered_by == user.id:
        return
    now = datetime.utcnow()
    grant = (
        db.query(AdhocRunShareGrant.id)
        .join(AdhocRunShare, AdhocRunShare.id == AdhocRunShareGrant.share_id)
        .filter(
            AdhocRunShareGrant.run_id == row.id,
            AdhocRunShareGrant.user_id == user.id,
            AdhocRunShare.revoked_at.is_(None),
            AdhocRunShare.expires_at > now,
        )
        .first()
    )
    if not grant:
        raise HTTPException(status_code=403, detail="仅可查看本人或已获分享授权的运行记录")


def _serialize_share(row: AdhocRunShare) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "created_by": row.created_by,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
        "revoked_by": row.revoked_by,
        "created_at": row.created_at,
        "active": row.revoked_at is None and row.expires_at > datetime.utcnow(),
    }


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


@router.get("/share-links/{token}")
def redeem_adhoc_run_share(
    token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    share = (
        db.query(AdhocRunShare)
        .filter(AdhocRunShare.token_hash == token_hash)
        .first()
    )
    if not share:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    if share.revoked_at is not None:
        raise HTTPException(status_code=410, detail="分享链接已撤销")
    if share.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=410, detail="分享链接已过期")
    run = db.query(AdhocRun).filter(AdhocRun.id == share.run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    # A link never bypasses current workspace membership or source permission.
    assert_workspace_access(db, current_user, run.workspace_id)
    if run.source not in _allowed_sources(db, current_user, run.workspace_id):
        raise HTTPException(status_code=403, detail="无权查看该来源的运行记录")
    grant = (
        db.query(AdhocRunShareGrant)
        .filter(
            AdhocRunShareGrant.share_id == share.id,
            AdhocRunShareGrant.user_id == current_user.id,
        )
        .first()
    )
    if not grant:
        grant = AdhocRunShareGrant(
            share_id=share.id,
            run_id=run.id,
            user_id=current_user.id,
        )
        db.add(grant)
        try:
            db.commit()
            db.refresh(grant)
        except IntegrityError:
            db.rollback()
            grant = (
                db.query(AdhocRunShareGrant)
                .filter(
                    AdhocRunShareGrant.share_id == share.id,
                    AdhocRunShareGrant.user_id == current_user.id,
                )
                .one()
            )
    return {
        "share": _serialize_share(share),
        "grant_id": grant.id,
        "run": serialize_adhoc_run(run, include_result=True),
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
        .limit(limit + 1)
        .all()
    )
    has_more = len(chunks) > limit
    chunks = chunks[:limit]
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
        "has_more": has_more,
    }


@router.get("/{run_id}/events")
def get_adhoc_run_events(
    run_id: int,
    after_log_seq: int = Query(0, ge=0),
    statement_version: Optional[str] = None,
    log_limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Drain logs and statement metadata through one incremental request."""
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)
    log_rows = (
        db.query(AdhocRunLogChunk)
        .filter(
            AdhocRunLogChunk.run_id == run_id,
            AdhocRunLogChunk.seq > after_log_seq,
        )
        .order_by(AdhocRunLogChunk.seq)
        .limit(log_limit + 1)
        .all()
    )
    logs_have_more = len(log_rows) > log_limit
    log_rows = log_rows[:log_limit]
    statements = list_run_statements(db, run_id)
    current_statement_version = statement_collection_version(statements)
    statement_changed = statement_version != current_statement_version
    next_log_seq = log_rows[-1].seq if log_rows else after_log_seq
    still_running = row.status in ACTIVE_RUN_STATUSES
    now = datetime.utcnow()
    queue_end = row.started_at or row.finished_at or now
    execution_end = row.finished_at or now
    queue_duration_ms = (
        max(0, int((queue_end - row.created_at).total_seconds() * 1000))
        if row.created_at
        else None
    )
    execution_duration_ms = (
        max(0, int((execution_end - row.started_at).total_seconds() * 1000))
        if row.started_at
        else None
    )
    return {
        "run_id": row.id,
        "status": row.status,
        "version": int(row.status_version or 0),
        "timing": {
            "created_at": row.created_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "queue_duration_ms": queue_duration_ms,
            "execution_duration_ms": execution_duration_ms,
        },
        "statement_version": current_statement_version,
        "logs": {
            "chunks": [
                {
                    "seq": item.seq,
                    "stream": item.stream,
                    "content": item.content,
                    "created_at": item.created_at,
                }
                for item in log_rows
            ],
            "next_seq": next_log_seq,
            "has_more": logs_have_more,
        },
        "statements": (
            [serialize_run_statement(item) for item in statements]
            if statement_changed
            else []
        ),
        "statements_changed": statement_changed,
        # Keep draining after a terminal transition when this page did not fit all logs.
        "poll_required": still_running or logs_have_more,
    }


@router.get("/{run_id}/statements")
def get_adhoc_run_statements(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)
    statements = list_run_statements(db, run_id)
    return {
        "run_id": run_id,
        "status": row.status,
        "version": int(row.status_version or 0),
        "statement_version": statement_collection_version(statements),
        "statements": [serialize_run_statement(item) for item in statements],
    }


@router.get("/{run_id}/statements/{statement_index}/rows")
def get_adhoc_statement_rows(
    run_id: int,
    statement_index: int,
    cursor: Optional[str] = None,
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)
    try:
        page = paginate_statement_rows(
            db, run_id, statement_index, cursor=cursor, limit=limit
        )
    except ValueError as exc:
        status_code = 400 if "游标" in str(exc) else 404
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return {
        "run_id": run_id,
        "run_status": row.status,
        "run_version": int(row.status_version or 0),
        **page,
    }


@router.get("/{run_id}/statements/{statement_index}/chunks")
def get_adhoc_statement_chunks(
    run_id: int,
    statement_index: int,
    after_cursor: int = Query(-1, ge=-1),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, row)
    try:
        page = paginate_statement_result_chunks(
            db,
            run_id,
            statement_index,
            after_cursor=after_cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **{key: value for key, value in page.items() if key != "chunks"},
        "chunks": [
            {
                "cursor": item.chunk_index,
                "row_offset": int(item.row_offset or 0),
                "row_count": int(item.row_count or 0),
                "rows": list((item.payload or {}).get("rows") or []),
                "created_at": item.created_at,
            }
            for item in page["chunks"]
        ],
    }


@router.post("/{run_id}/statements/{statement_index}/rows/query")
def query_adhoc_statement_rows(
    run_id: int,
    statement_index: int,
    request: AdhocRowQuery,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, run)
    try:
        page = query_statement_rows(
            db,
            run_id,
            statement_index,
            search=request.search,
            filters=[item.model_dump(exclude_unset=True) for item in request.filters],
            sort=[item.model_dump() for item in request.sort],
            cursor=request.cursor,
            limit=request.limit,
            statement_version=request.statement_version,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "版本" in detail or "尚未完成" in detail else 400
        if "不存在" in detail:
            status_code = 404
        if status_code == 409 and "current=" in detail:
            current = detail.rsplit("current=", 1)[-1].strip()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "statement_version_mismatch",
                    "message": "语句版本已变化，请回到第 1 页后重试",
                    "current_statement_version": current,
                },
            ) from exc
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return {
        "run_id": run_id,
        "run_status": run.status,
        "run_version": int(run.status_version or 0),
        **page,
    }


@router.post("/{run_id}/statements/{statement_index}/explain")
def explain_adhoc_statement(
    run_id: int,
    statement_index: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.adhoc_explain import capture_statement_plan

    run = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, run)
    statement = (
        db.query(AdhocRunStatement)
        .filter(
            AdhocRunStatement.run_id == run_id,
            AdhocRunStatement.statement_index == statement_index,
        )
        .first()
    )
    if not statement:
        raise HTTPException(status_code=404, detail="语句不存在")
    datasource = (
        db.query(DataSource)
        .filter(
            DataSource.id == run.datasource_id,
            DataSource.workspace_id == run.workspace_id,
        )
        .first()
    )
    if not datasource:
        raise HTTPException(status_code=409, detail="运行记录的数据源已不可用")
    try:
        snapshot = capture_statement_plan(
            db, datasource=datasource, statement=statement
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Explain 执行失败: {str(exc)[:500]}") from exc
    from app.services.audit import log_action

    log_action(
        db,
        current_user.id,
        "adhoc_explain",
        "adhoc_run_statement",
        resource_id=statement.id,
        workspace_id=run.workspace_id,
        detail={
            "run_id": run.id,
            "statement_index": statement.statement_index,
            "analyze": False,
        },
    )
    return {
        "run_id": run_id,
        "statement_index": statement_index,
        "plan_snapshot": snapshot,
        "statement": serialize_run_statement(statement),
    }


def _export_with_access(
    db: Session,
    current_user: User,
    run_id: int,
    export_id: int,
    *,
    require_owner: bool = False,
) -> tuple[AdhocRun, AdhocRunExport]:
    run = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, run)
    export = (
        db.query(AdhocRunExport)
        .filter(
            AdhocRunExport.id == export_id,
            AdhocRunExport.run_id == run_id,
        )
        .first()
    )
    if not export:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if (
        require_owner
        and export.requested_by != current_user.id
        and not workspace_data_full_control(db, current_user, run.workspace_id)
    ):
        raise HTTPException(status_code=403, detail="仅导出申请人可访问该文件")
    return run, export


@router.post("/{run_id}/exports", status_code=202)
def create_adhoc_run_export(
    run_id: int,
    request: AdhocExportCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.adhoc_run_export import create_export, serialize_export

    run = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, run)
    statement_query = db.query(AdhocRunStatement).filter(
        AdhocRunStatement.run_id == run_id
    )
    if request.statement_index is not None:
        statement_query = statement_query.filter(
            AdhocRunStatement.statement_index == request.statement_index
        )
    else:
        statement_query = statement_query.filter(
            AdhocRunStatement.status == "success"
        ).order_by(desc(AdhocRunStatement.statement_index))
    statement = statement_query.first()
    if not statement:
        raise HTTPException(status_code=404, detail="没有可导出的语句结果")
    try:
        export = create_export(
            db,
            run=run,
            statement=statement,
            requested_by=current_user.id,
            export_format=request.format,
            query_spec={
                "search": request.search,
                "filters": [
                    item.model_dump(exclude_unset=True) for item in request.filters
                ],
                "sort": [item.model_dump() for item in request.sort],
            },
            statement_version=request.statement_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return serialize_export(export)


@router.get("/{run_id}/exports/{export_id}")
def get_adhoc_run_export(
    run_id: int,
    export_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.adhoc_run_export import serialize_export

    _, export = _export_with_access(db, current_user, run_id, export_id)
    return serialize_export(export)


@router.get("/{run_id}/exports/{export_id}/download")
def download_adhoc_run_export(
    run_id: int,
    export_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.adhoc_run_export import (
        export_content_type,
        stream_database_export,
        stream_s3_export,
    )

    _, export = _export_with_access(db, current_user, run_id, export_id)
    if export.expires_at and export.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=410, detail="导出文件已过期")
    if export.status != "success":
        raise HTTPException(status_code=409, detail=f"导出尚不可下载：{export.status}")
    stream = (
        stream_s3_export(export.storage_key)
        if export.storage_key
        else stream_database_export(export.id)
    )
    file_name = export.file_name or f"adhoc-run-{run_id}.{export.format}"
    return StreamingResponse(
        stream,
        media_type=export_content_type(export.format),
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.post("/{run_id}/exports/{export_id}/cancel")
@router.delete("/{run_id}/exports/{export_id}")
def cancel_adhoc_run_export(
    run_id: int,
    export_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.adhoc_run_export import cancel_export, serialize_export

    _, export = _export_with_access(
        db, current_user, run_id, export_id, require_owner=True
    )
    return serialize_export(cancel_export(db, export))


@router.post("/{run_id}/shares", status_code=201)
def create_adhoc_run_share(
    run_id: int,
    request: AdhocShareCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.core.config import settings

    run = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, run)
    if run.triggered_by != current_user.id and not workspace_data_full_control(
        db, current_user, run.workspace_id
    ):
        raise HTTPException(status_code=403, detail="仅运行发起人或空间管理员可创建分享链接")
    token = secrets.token_urlsafe(32)
    ttl_hours = request.ttl_hours or max(1, int(settings.ADHOC_SHARE_TTL_HOURS))
    share = AdhocRunShare(
        run_id=run.id,
        created_by=current_user.id,
        token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return {**_serialize_share(share), "token": token}


@router.delete("/{run_id}/shares/{share_id}")
def revoke_adhoc_run_share(
    run_id: int,
    share_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(AdhocRun).filter(AdhocRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    _assert_can_view_run(db, current_user, run)
    share = (
        db.query(AdhocRunShare)
        .filter(AdhocRunShare.id == share_id, AdhocRunShare.run_id == run_id)
        .first()
    )
    if not share:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    if share.created_by != current_user.id and not workspace_data_full_control(
        db, current_user, run.workspace_id
    ):
        raise HTTPException(status_code=403, detail="仅创建者或空间管理员可撤销分享")
    if share.revoked_at is None:
        share.revoked_at = datetime.utcnow()
        share.revoked_by = current_user.id
        db.commit()
        db.refresh(share)
    return _serialize_share(share)


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
    payload = row.request_payload if isinstance(row.request_payload, dict) else {}
    return {
        "run_id": row.id,
        "status": row.status,
        "cancel_outcome": payload.get("cancel_outcome"),
    }

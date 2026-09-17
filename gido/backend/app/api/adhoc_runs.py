# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""运行历史：数据开发试跑与数据探查交互式执行记录。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core import perm_codes as PC
from app.core.access import user_has_any
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import (
    AdhocRun,
    AdhocRunExport,
    AdhocRunLogChunk,
    AdhocRunStatement,
    DataSource,
    User,
)
from app.services.adhoc_run_store import (
    list_run_statements,
    paginate_statement_rows,
    paginate_statement_result_chunks,
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
    return {
        "run_id": row.id,
        "status": row.status,
        "version": int(row.status_version or 0),
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


def _export_with_access(
    db: Session, current_user: User, run_id: int, export_id: int
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
    if export.requested_by != current_user.id:
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

    _, export = _export_with_access(db, current_user, run_id, export_id)
    return serialize_export(cancel_export(db, export))


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

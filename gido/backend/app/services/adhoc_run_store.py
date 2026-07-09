# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""交互式执行记录（数据开发试跑 / 数据探查）持久化。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.workspace import AdhocRun

logger = logging.getLogger(__name__)

ADHOC_RESULT_PREVIEW_ROWS = 200


def truncate_result_preview(result: Optional[Dict[str, Any]], max_rows: int = ADHOC_RESULT_PREVIEW_ROWS) -> Optional[Dict[str, Any]]:
    """将 SQL 结果截断为可入库的预览结构。"""
    if not result or not isinstance(result, dict):
        return None
    columns = list(result.get("columns") or [])
    column_types = list(result.get("column_types") or [])
    rows = list(result.get("rows") or [])
    total = int(result.get("total") if result.get("total") is not None else len(rows))
    truncated = bool(result.get("truncated")) or len(rows) > max_rows
    preview_rows = rows[:max_rows]
    return {
        "columns": columns,
        "column_types": column_types,
        "rows": preview_rows,
        "total": total,
        "truncated": truncated,
    }


def save_adhoc_run(
    db: Session,
    *,
    workspace_id: int,
    source: str,
    triggered_by: Optional[int],
    status: str,
    sql_text: Optional[str] = None,
    datasource_id: Optional[int] = None,
    object_name: Optional[str] = None,
    node_id: Optional[int] = None,
    node_instance_id: Optional[int] = None,
    error_message: Optional[str] = None,
    log_content: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> Optional[AdhocRun]:
    """写入一条运行历史；失败时记录日志但不抛出，避免阻断主执行路径。"""
    try:
        preview = truncate_result_preview(result)
        rows_returned = 0
        if preview is not None:
            rows_returned = int(preview.get("total") or len(preview.get("rows") or []))
        st = started_at or datetime.utcnow()
        ft = finished_at or datetime.utcnow()
        duration_ms = None
        if st and ft:
            duration_ms = max(0, int((ft - st).total_seconds() * 1000))
        row = AdhocRun(
            workspace_id=workspace_id,
            source=(source or "").strip().lower()[:32],
            triggered_by=triggered_by,
            datasource_id=datasource_id,
            object_name=(object_name or "")[:256] or None,
            node_id=node_id,
            node_instance_id=node_instance_id,
            sql_text=sql_text,
            status=(status or "success")[:32],
            error_message=error_message,
            log_content=log_content,
            result_preview=preview,
            rows_returned=rows_returned,
            duration_ms=duration_ms,
            started_at=st,
            finished_at=ft,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        db.rollback()
        logger.warning("save_adhoc_run failed workspace=%s source=%s", workspace_id, source, exc_info=True)
        return None


def serialize_adhoc_run(row: AdhocRun, *, include_result: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "source": row.source,
        "triggered_by": row.triggered_by,
        "datasource_id": row.datasource_id,
        "object_name": row.object_name,
        "node_id": row.node_id,
        "node_instance_id": row.node_instance_id,
        "sql_text": row.sql_text,
        "status": row.status,
        "error_message": row.error_message,
        "log_content": row.log_content,
        "rows_returned": row.rows_returned or 0,
        "duration_ms": row.duration_ms,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
        "result_truncated": bool((row.result_preview or {}).get("truncated")) if isinstance(row.result_preview, dict) else False,
    }
    if include_result:
        data["result_preview"] = row.result_preview
    return data

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
SQL_SUMMARY_MAX_LEN = 72


def summarize_sql(sql: Optional[str], max_len: int = SQL_SUMMARY_MAX_LEN) -> Optional[str]:
    """
    列表用 SQL 摘要：动词 + 主表，例如「SELECT · ads_foo」，
    避免把整段列清单铺在列表上。
    """
    import re

    if not sql or not str(sql).strip():
        return None
    lines: list[str] = []
    for raw in str(sql).splitlines():
        line = raw
        dash = line.find("--")
        if dash >= 0:
            line = line[:dash]
        s = line.strip()
        if not s:
            continue
        lines.append(s)
    compact = " ".join(" ".join(lines).split())
    if not compact:
        return None

    # 多语句：取第一条有意义的
    first = compact.split(";")[0].strip() or compact
    stmt_count = len([p for p in compact.split(";") if p.strip()])

    verb_m = re.match(
        r"(?i)\b(with|select|insert|update|delete|create|drop|alter|truncate|describe|desc|show|use|explain)\b",
        first,
    )
    verb = (verb_m.group(1).upper() if verb_m else "SQL")
    if verb == "DESC":
        verb = "DESCRIBE"

    table_m = re.search(
        r"(?i)\b(?:from|into|update|table|describe|desc)\s+([`\"\w$.]+)",
        first,
    )
    table = None
    if table_m:
        table = table_m.group(1).replace("`", "").replace('"', "")

    if table:
        summary = f"{verb} · {table}"
    else:
        # 无表：短截断正文，仍避免长列清单
        short = first
        if len(short) > max_len:
            short = short[: max_len - 1] + "…"
        summary = short

    if stmt_count > 1:
        summary = f"{summary} 等{stmt_count}段"

    if len(summary) > max_len:
        return summary[: max_len - 1] + "…"
    return summary


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


def serialize_adhoc_run(
    row: AdhocRun,
    *,
    include_result: bool = False,
    include_sql: bool = True,
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "source": row.source,
        "triggered_by": row.triggered_by,
        "datasource_id": row.datasource_id,
        "object_name": row.object_name,
        "node_id": row.node_id,
        "node_instance_id": row.node_instance_id,
        "sql_summary": summarize_sql(row.sql_text),
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
    if include_sql:
        data["sql_text"] = row.sql_text
    if include_result:
        data["result_preview"] = row.result_preview
    return data

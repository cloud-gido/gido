# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""交互式执行记录（数据开发试跑 / 数据探查）持久化。"""
from __future__ import annotations

import logging
import hashlib
import json
import base64
from decimal import Decimal, InvalidOperation
from functools import cmp_to_key
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.workspace import AdhocRun, AdhocRunResultChunk, AdhocRunStatement

logger = logging.getLogger(__name__)

ADHOC_RESULT_PREVIEW_ROWS = 200
SQL_SUMMARY_MAX_LEN = 72
SQL_PREVIEW_MAX_LINES = 40
SQL_PREVIEW_MAX_CHARS = 2048


class LeaseFenceError(InterruptedError):
    """The caller no longer owns the run lease."""


def _owned_run(db: Session, run_id: int, lease_token: str) -> AdhocRun:
    query = db.query(AdhocRun).filter(
        AdhocRun.id == run_id,
        AdhocRun.lease_token == lease_token,
        AdhocRun.status.in_(("running", "cancel_requested")),
    )
    # Serialize lease validation with all writes belonging to that lease. Without
    # this lock, a reclaimer can replace the lease between SELECT and COMMIT.
    if db.bind and db.bind.dialect.name in ("postgresql", "mysql"):
        query = query.with_for_update()
    row = query.first()
    if not row:
        db.rollback()
        raise LeaseFenceError("运行租约已失效")
    return row


def _statement_type(sql: str) -> Optional[str]:
    lines = _sql_content_lines(sql)
    if not lines:
        return None
    first = lines[0].lstrip("(").strip().split(None, 1)
    return first[0].upper()[:32] if first else None


def initialize_run_statements(
    db: Session,
    run_id: int,
    statements: Iterable[str],
    lease_token: str,
) -> List[AdhocRunStatement]:
    """Pre-create every statement before execution starts."""
    run = _owned_run(db, run_id, lease_token)
    existing = {
        item.statement_index: item
        for item in db.query(AdhocRunStatement)
        .filter(AdhocRunStatement.run_id == run_id)
        .all()
    }
    if existing and any(item.status != "pending" for item in existing.values()):
        statement_ids = [item.id for item in existing.values()]
        db.query(AdhocRunResultChunk).filter(
            AdhocRunResultChunk.statement_id.in_(statement_ids)
        ).delete(synchronize_session=False)
        db.query(AdhocRunStatement).filter(
            AdhocRunStatement.run_id == run_id
        ).delete(synchronize_session=False)
        db.flush()
        existing = {}
        run.result_preview = None
        run.rows_returned = 0
    rows: List[AdhocRunStatement] = []
    for index, sql in enumerate(statements):
        text = str(sql or "").strip()
        row = existing.get(index)
        if row is None:
            row = AdhocRunStatement(
                run_id=run_id,
                statement_index=index,
                statement_type=_statement_type(text),
                sql_text=text,
                sql_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                status="pending",
            )
            db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def _owned_statement(
    db: Session,
    run_id: int,
    statement_index: int,
    lease_token: str,
) -> AdhocRunStatement:
    _owned_run(db, run_id, lease_token)
    row = (
        db.query(AdhocRunStatement)
        .filter(
            AdhocRunStatement.run_id == run_id,
            AdhocRunStatement.statement_index == statement_index,
        )
        .first()
    )
    if not row:
        raise ValueError(f"语句 {statement_index} 尚未初始化")
    return row


def _encode_result_rows(rows: List[List[Any]]) -> bytes:
    return json.dumps(
        {"rows": rows},
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _fit_result_rows_to_bytes(
    rows: List[List[Any]], max_bytes: int
) -> tuple[List[List[Any]], bytes]:
    raw = _encode_result_rows(rows)
    if len(raw) <= max_bytes:
        return rows, raw
    low, high = 0, len(rows)
    best_raw = _encode_result_rows([])
    while low < high:
        middle = (low + high + 1) // 2
        candidate = _encode_result_rows(rows[:middle])
        if len(candidate) <= max_bytes:
            low = middle
            best_raw = candidate
        else:
            high = middle - 1
    return rows[:low], best_raw


def start_run_statement(
    db: Session, run_id: int, statement_index: int, lease_token: str
) -> AdhocRunStatement:
    row = _owned_statement(db, run_id, statement_index, lease_token)
    row.status = "running"
    row.started_at = row.started_at or datetime.utcnow()
    row.finished_at = None
    row.error_message = None
    db.commit()
    db.refresh(row)
    return row


def append_statement_result_chunk(
    db: Session,
    run_id: int,
    statement_index: int,
    rows: Iterable[Iterable[Any]],
    lease_token: str,
    *,
    columns: Optional[Iterable[str]] = None,
    column_types: Optional[Iterable[str]] = None,
    fields: Optional[Iterable[Dict[str, Any]]] = None,
    truncated: bool = False,
    max_rows: Optional[int] = None,
    max_bytes: Optional[int] = None,
) -> Optional[AdhocRunResultChunk]:
    """Append one immutable chunk and return it; limits are per statement."""
    statement = _owned_statement(db, run_id, statement_index, lease_token)
    materialized = [list(item) for item in rows]
    submitted_count = len(materialized)
    remaining_rows = (
        max(0, int(max_rows) - int(statement.result_rows or 0))
        if max_rows is not None
        else len(materialized)
    )
    materialized = materialized[:remaining_rows]
    if not materialized:
        schema = dict(statement.column_schema or {})
        if columns is not None:
            schema["columns"] = list(columns)
            schema["column_types"] = list(column_types or [])
            schema["fields"] = list(fields or _legacy_fields(schema))
        schema["truncated"] = bool(schema.get("truncated")) or bool(truncated) or submitted_count > 0
        statement.column_schema = schema
        db.commit()
        return None

    raw = _encode_result_rows(materialized)
    if max_bytes is not None:
        remaining_bytes = max(0, int(max_bytes) - int(statement.result_bytes or 0))
        materialized, raw = _fit_result_rows_to_bytes(
            materialized,
            remaining_bytes,
        )
        if not materialized:
            schema = dict(statement.column_schema or {})
            if columns is not None:
                schema["columns"] = list(columns)
                schema["column_types"] = list(column_types or [])
                schema["fields"] = list(fields or _legacy_fields(schema))
            schema["truncated"] = True
            statement.column_schema = schema
            db.commit()
            return None

    chunk_index = int(statement.result_chunk_count or 0)
    chunk = AdhocRunResultChunk(
        statement_id=statement.id,
        chunk_index=chunk_index,
        row_offset=int(statement.result_rows or 0),
        row_count=len(materialized),
        payload={"rows": materialized},
        payload_bytes=len(raw),
    )
    db.add(chunk)
    statement.result_rows = int(statement.result_rows or 0) + len(materialized)
    statement.result_bytes = int(statement.result_bytes or 0) + len(raw)
    statement.result_chunk_count = chunk_index + 1
    schema = dict(statement.column_schema or {})
    if columns is not None:
        schema["columns"] = list(columns)
        schema["column_types"] = list(column_types or [])
        schema["fields"] = list(fields or _legacy_fields(schema))
    schema["truncated"] = (
        bool(schema.get("truncated"))
        or bool(truncated)
        or len(materialized) < submitted_count
    )
    statement.column_schema = schema
    db.commit()
    db.refresh(chunk)
    return chunk


def finish_run_statement(
    db: Session,
    run_id: int,
    statement_index: int,
    lease_token: str,
    status: str,
    *,
    affected_rows: Optional[int] = None,
    error_message: Optional[str] = None,
    columns: Optional[Iterable[str]] = None,
    column_types: Optional[Iterable[str]] = None,
    fields: Optional[Iterable[Dict[str, Any]]] = None,
    execution_metrics: Optional[Dict[str, Any]] = None,
    query_id: Optional[str] = None,
    truncated: bool = False,
) -> AdhocRunStatement:
    if status not in ("success", "failed", "skipped"):
        raise ValueError(f"非法语句终态: {status}")
    row = _owned_statement(db, run_id, statement_index, lease_token)
    row.status = status
    row.affected_rows = affected_rows
    row.error_message = (error_message or "")[:4000] or None
    row.finished_at = datetime.utcnow()
    if columns is not None:
        schema = dict(row.column_schema or {})
        schema.update(
            columns=list(columns),
            column_types=list(column_types or []),
            truncated=bool(schema.get("truncated")) or bool(truncated),
        )
        if fields is not None:
            schema["fields"] = list(fields)
        if not schema.get("fields"):
            schema["fields"] = _legacy_fields(schema)
        row.column_schema = schema
    metrics = dict(row.execution_metrics or {})
    metrics.update(execution_metrics or {})
    metrics.setdefault("status", status)
    metrics.setdefault("rows_returned", int(row.result_rows or 0))
    metrics.setdefault("result_bytes", int(row.result_bytes or 0))
    metrics.setdefault("chunk_count", int(row.result_chunk_count or 0))
    if affected_rows is not None:
        metrics.setdefault("affected_rows", int(affected_rows))
    if row.started_at:
        metrics.setdefault(
            "duration_ms",
            max(0, int((row.finished_at - row.started_at).total_seconds() * 1000)),
        )
    row.execution_metrics = metrics
    if query_id is not None:
        row.query_id = str(query_id)[:256] or None
    db.commit()
    db.refresh(row)
    run = _owned_run(db, run_id, lease_token)
    preview = build_result_preview_from_statements(db, run_id)
    if preview is not None:
        run.result_preview = preview
        run.rows_returned = int(preview.get("total") or 0)
        run.last_progress_at = datetime.utcnow()
        db.commit()
    return row


def complete_run_statement_success(db: Session, run_id: int, statement_index: int, lease_token: str, **kwargs):
    return finish_run_statement(db, run_id, statement_index, lease_token, "success", **kwargs)


def complete_run_statement_failed(db: Session, run_id: int, statement_index: int, lease_token: str, error_message: str):
    return finish_run_statement(
        db, run_id, statement_index, lease_token, "failed", error_message=error_message
    )


def complete_run_statement_skipped(db: Session, run_id: int, statement_index: int, lease_token: str, error_message: Optional[str] = None):
    return finish_run_statement(
        db, run_id, statement_index, lease_token, "skipped", error_message=error_message
    )


def list_run_statements(db: Session, run_id: int) -> List[AdhocRunStatement]:
    return (
        db.query(AdhocRunStatement)
        .filter(AdhocRunStatement.run_id == run_id)
        .order_by(AdhocRunStatement.statement_index, AdhocRunStatement.id)
        .all()
    )


def _semantic_type_from_raw(raw_type: str) -> str:
    from app.services.sql_readonly import semantic_type

    return semantic_type(raw_type)


def _legacy_fields(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    columns = list(schema.get("columns") or [])
    types = list(schema.get("column_types") or [])
    return [
        {
            "name": str(name),
            "raw_type": str(types[index]) if index < len(types) else "unknown",
            "semantic_type": _semantic_type_from_raw(
                str(types[index]) if index < len(types) else "unknown"
            ),
            "nullable": None,
            "precision": None,
            "scale": None,
        }
        for index, name in enumerate(columns)
    ]


def statement_snapshot_version(row: AdhocRunStatement) -> str:
    state = (
        int(row.id),
        str(row.sql_hash or ""),
        str(row.status),
        int(row.result_rows or 0),
        int(row.result_bytes or 0),
        int(row.result_chunk_count or 0),
        row.updated_at.isoformat() if row.updated_at else "",
    )
    raw = json.dumps(state, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def serialize_run_statement(row: AdhocRunStatement) -> Dict[str, Any]:
    schema = row.column_schema if isinstance(row.column_schema, dict) else {}
    return {
        "id": row.id,
        "run_id": row.run_id,
        "index": row.statement_index,
        "statement_type": row.statement_type,
        "sql": row.sql_text,
        "status": row.status,
        "columns": list(schema.get("columns") or []),
        "column_types": list(schema.get("column_types") or []),
        "fields": list(schema.get("fields") or _legacy_fields(schema)),
        "execution_metrics": row.execution_metrics
        if isinstance(row.execution_metrics, dict)
        else None,
        "query_id": row.query_id,
        "plan_snapshot": row.plan_snapshot
        if isinstance(row.plan_snapshot, dict)
        else None,
        "statement_version": statement_snapshot_version(row),
        "affected_rows": row.affected_rows,
        "total": int(row.result_rows or 0),
        "result_bytes": int(row.result_bytes or 0),
        "chunk_count": int(row.result_chunk_count or 0),
        "truncated": bool(schema.get("truncated")),
        "error": row.error_message,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


def statement_collection_version(statements: Iterable[AdhocRunStatement]) -> str:
    """Opaque version for incremental statement metadata polling."""
    state = [
        (
            int(row.id),
            int(row.statement_index),
            str(row.status),
            int(row.result_rows or 0),
            int(row.result_bytes or 0),
            int(row.result_chunk_count or 0),
            row.updated_at.isoformat() if row.updated_at else "",
        )
        for row in statements
    ]
    raw = json.dumps(state, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _encode_row_cursor(chunk_index: int, row_index: int) -> str:
    raw = f"{int(chunk_index)}:{int(row_index)}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_row_cursor(cursor: Optional[str]) -> tuple[int, int]:
    if not cursor:
        return 0, 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        chunk_index, row_index = base64.urlsafe_b64decode(padded).decode("ascii").split(":", 1)
        chunk_value, row_value = int(chunk_index), int(row_index)
        if chunk_value < 0 or row_value < 0:
            raise ValueError
        return chunk_value, row_value
    except (ValueError, UnicodeError) as exc:
        raise ValueError("无效的结果分页游标") from exc


def paginate_statement_rows(
    db: Session,
    run_id: int,
    statement_index: int,
    *,
    cursor: Optional[str] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    """Flatten immutable ORM chunks into a stable row-cursor page."""
    statement = (
        db.query(AdhocRunStatement)
        .filter(
            AdhocRunStatement.run_id == run_id,
            AdhocRunStatement.statement_index == statement_index,
        )
        .first()
    )
    if not statement:
        raise ValueError("语句不存在")
    page_size = min(max(int(limit), 1), 5000)
    chunk_index, row_index = _decode_row_cursor(cursor)
    chunks = (
        db.query(AdhocRunResultChunk)
        .filter(
            AdhocRunResultChunk.statement_id == statement.id,
            AdhocRunResultChunk.chunk_index >= chunk_index,
        )
        .order_by(AdhocRunResultChunk.chunk_index, AdhocRunResultChunk.id)
        .all()
    )
    rows: List[List[Any]] = []
    next_position: Optional[tuple[int, int]] = None
    for chunk in chunks:
        chunk_rows = list((chunk.payload or {}).get("rows") or [])
        start = row_index if chunk.chunk_index == chunk_index else 0
        for offset in range(start, len(chunk_rows)):
            if len(rows) >= page_size:
                next_position = (chunk.chunk_index, offset)
                break
            rows.append(list(chunk_rows[offset]))
        if next_position is not None:
            break
        row_index = 0
    has_more = next_position is not None
    schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
    return {
        "statement_id": statement.id,
        "statement_index": statement.statement_index,
        "statement_status": statement.status,
        "snapshot": statement.status in ("success", "failed", "skipped"),
        "columns": list(schema.get("columns") or []),
        "column_types": list(schema.get("column_types") or []),
        "fields": list(schema.get("fields") or _legacy_fields(schema)),
        "rows": rows,
        "total": int(statement.result_rows or 0),
        "truncated": bool(schema.get("truncated")),
        "next_cursor": (
            _encode_row_cursor(*next_position) if next_position is not None else None
        ),
        "has_more": has_more,
    }


_QUERY_OPERATORS = frozenset(
    ("eq", "ne", "in", "contains", "starts_with", "gt", "gte", "lt", "lte", "is_null", "not_null")
)


def _typed_value(value: Any, semantic_type: str) -> Any:
    if value is None:
        return None
    if semantic_type == "number":
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"无法将 {value!r} 转换为数值") from exc
    if semantic_type == "boolean":
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        raise ValueError(f"无法将 {value!r} 转换为布尔值")
    if semantic_type == "datetime":
        return str(value).replace("T", " ")
    return str(value)


def _query_digest(search: Any, filters: Any, sort: Any) -> str:
    raw = json.dumps(
        {"search": search or "", "filters": filters or [], "sort": sort or []},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _query_cursor(offset: int, digest: str, version: str) -> str:
    raw = json.dumps(
        {"offset": offset, "digest": digest, "version": version},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _query_offset(cursor: Optional[str], digest: str, version: str) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        offset = int(data["offset"])
        if offset < 0 or data["digest"] != digest or data["version"] != version:
            raise ValueError
        return offset
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("无效或与当前查询不匹配的结果游标") from exc


def query_statement_rows(
    db: Session,
    run_id: int,
    statement_index: int,
    *,
    search: Optional[str] = None,
    filters: Optional[List[Dict[str, Any]]] = None,
    sort: Optional[List[Dict[str, Any]]] = None,
    cursor: Optional[str] = None,
    limit: int = 500,
    statement_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Filter and sort the materialized, version-bound result snapshot.

    Industry-aligned progressive contract:
    - First page (no cursor): soft-upgrade when the client version is missing/stale so
      progressive chunk appends never blank the grid with 409.
    - Cursor pages: require an exact version match so offsets stay consistent.
    """
    statement = (
        db.query(AdhocRunStatement)
        .filter(
            AdhocRunStatement.run_id == run_id,
            AdhocRunStatement.statement_index == statement_index,
        )
        .first()
    )
    if not statement:
        raise ValueError("语句不存在")
    version = statement_snapshot_version(statement)
    requested = (statement_version or "").strip() or None
    using_cursor = bool((cursor or "").strip())
    if requested and requested != version:
        if using_cursor:
            raise ValueError(
                "语句版本已变化，请回到第 1 页后重试:"
                f"current={version}"
            )
        # Soft-upgrade: progressive materialization and terminal completion both bump
        # the snapshot version; page-1 reads should follow the latest snapshot.
    elif not requested and using_cursor:
        raise ValueError("分页游标查询必须携带 statement_version")
    schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
    columns = list(schema.get("columns") or [])
    fields = list(schema.get("fields") or _legacy_fields(schema))
    indexes: Dict[str, int] = {}
    for index, name in enumerate(columns):
        if name in indexes:
            raise ValueError(f"结果包含重复列名，无法查询: {name}")
        indexes[name] = index
    field_map = {
        str(field.get("name")): field for field in fields if isinstance(field, dict)
    }
    filter_items = list(filters or [])
    sort_items = list(sort or [])
    if len(filter_items) > 20:
        raise ValueError("最多支持 20 个筛选条件")
    if len(sort_items) > 2:
        raise ValueError("最多支持两列排序")
    for item in filter_items:
        column = str(item.get("column") or "")
        operator = str(item.get("operator") or "").lower()
        if column not in indexes:
            raise ValueError(f"未知结果列: {column}")
        if operator not in _QUERY_OPERATORS:
            raise ValueError(f"不支持的筛选操作符: {operator}")
        semantic = str(field_map.get(column, {}).get("semantic_type") or "string")
        if operator in ("contains", "starts_with") and semantic not in ("string", "json"):
            raise ValueError(f"列 {column} 不支持文本筛选")
        if operator in ("gt", "gte", "lt", "lte") and semantic in ("boolean", "json", "binary"):
            raise ValueError(f"列 {column} 不支持范围筛选")
        if operator == "in" and not isinstance(item.get("value"), list):
            raise ValueError(f"筛选 {column}/in 的 value 必须是数组")
        if operator not in ("is_null", "not_null") and "value" not in item:
            raise ValueError(f"筛选 {column}/{operator} 缺少 value")
    for item in sort_items:
        column = str(item.get("column") or "")
        direction = str(item.get("direction") or "").lower()
        if column not in indexes:
            raise ValueError(f"未知结果列: {column}")
        if direction not in ("asc", "desc"):
            raise ValueError(f"非法排序方向: {direction}")

    all_rows: List[tuple[int, List[Any]]] = []
    chunks = (
        db.query(AdhocRunResultChunk)
        .filter(AdhocRunResultChunk.statement_id == statement.id)
        .order_by(AdhocRunResultChunk.chunk_index, AdhocRunResultChunk.id)
        .all()
    )
    for chunk in chunks:
        for raw_row in list((chunk.payload or {}).get("rows") or []):
            all_rows.append((len(all_rows), list(raw_row)))
    needle = str(search or "").casefold()

    def matches(row: List[Any]) -> bool:
        if needle and not any(
            value is not None and needle in str(value).casefold() for value in row
        ):
            return False
        for item in filter_items:
            column = str(item["column"])
            operator = str(item["operator"]).lower()
            actual = row[indexes[column]] if indexes[column] < len(row) else None
            if operator == "is_null":
                if actual is not None:
                    return False
                continue
            if operator == "not_null":
                if actual is None:
                    return False
                continue
            if operator == "in":
                raw_values = list(item.get("value") or [])
                if actual is None:
                    if any(value is None for value in raw_values):
                        continue
                    return False
                semantic = str(field_map.get(column, {}).get("semantic_type") or "string")
                converted = _typed_value(actual, semantic)
                expected_values = [
                    _typed_value(value, semantic)
                    for value in raw_values
                    if value is not None
                ]
                if converted in expected_values:
                    continue
                return False
            if actual is None:
                if operator == "eq" and item.get("value") is None:
                    continue
                if operator == "ne" and item.get("value") is not None:
                    continue
                return False
            semantic = str(field_map.get(column, {}).get("semantic_type") or "string")
            expected = _typed_value(item.get("value"), semantic)
            converted = _typed_value(actual, semantic)
            if operator == "contains":
                ok = str(expected).casefold() in str(converted).casefold()
            elif operator == "starts_with":
                ok = str(converted).casefold().startswith(str(expected).casefold())
            elif operator == "eq":
                ok = converted == expected
            elif operator == "ne":
                ok = converted != expected
            elif operator == "gt":
                ok = converted > expected
            elif operator == "gte":
                ok = converted >= expected
            elif operator == "lt":
                ok = converted < expected
            else:
                ok = converted <= expected
            if not ok:
                return False
        return True

    selected = [item for item in all_rows if matches(item[1])]
    if sort_items:
        def compare(left: tuple[int, List[Any]], right: tuple[int, List[Any]]) -> int:
            for item in sort_items:
                column = str(item["column"])
                index = indexes[column]
                lhs = left[1][index] if index < len(left[1]) else None
                rhs = right[1][index] if index < len(right[1]) else None
                if lhs is None or rhs is None:
                    result = 0 if lhs is rhs else (1 if lhs is None else -1)
                else:
                    semantic = str(field_map.get(column, {}).get("semantic_type") or "string")
                    lhs_t, rhs_t = _typed_value(lhs, semantic), _typed_value(rhs, semantic)
                    result = (lhs_t > rhs_t) - (lhs_t < rhs_t)
                    if str(item["direction"]).lower() == "desc":
                        result = -result
                if result:
                    return result
            return left[0] - right[0]

        selected.sort(key=cmp_to_key(compare))
    digest = _query_digest(search, filter_items, sort_items)
    offset = _query_offset(cursor, digest, version)
    page_size = min(max(int(limit), 1), 5000)
    page = selected[offset : offset + page_size]
    next_offset = offset + len(page)
    has_more = next_offset < len(selected)
    return {
        "statement_id": statement.id,
        "statement_index": statement.statement_index,
        "statement_version": version,
        "version_upgraded": bool(requested and requested != version),
        "snapshot": True,
        "statement_status": statement.status,
        "columns": columns,
        "column_types": list(schema.get("column_types") or []),
        "fields": fields,
        "rows": [item[1] for item in page],
        "total": len(selected),
        "source_total": len(all_rows),
        "truncated": bool(schema.get("truncated")),
        "next_cursor": _query_cursor(next_offset, digest, version) if has_more else None,
        "has_more": has_more,
    }


def sample_statement_column_values(
    db: Session,
    run_id: int,
    statement_index: int,
    column: str,
    *,
    limit: int = 200,
    statement_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Sample distinct cell values for a column filter dropdown.

    Scans the materialized snapshot (not the warehouse). Page-1 style soft-upgrade
    applies when the client version is stale or missing.
    """
    statement = (
        db.query(AdhocRunStatement)
        .filter(
            AdhocRunStatement.run_id == run_id,
            AdhocRunStatement.statement_index == statement_index,
        )
        .first()
    )
    if not statement:
        raise ValueError("语句不存在")
    version = statement_snapshot_version(statement)
    requested = (statement_version or "").strip() or None
    schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
    columns = list(schema.get("columns") or [])
    if column not in columns:
        raise ValueError(f"未知结果列: {column}")
    col_index = columns.index(column)
    max_values = min(max(int(limit), 1), 500)
    seen: Dict[str, Any] = {}
    scanned = 0
    truncated = False
    chunks = (
        db.query(AdhocRunResultChunk)
        .filter(AdhocRunResultChunk.statement_id == statement.id)
        .order_by(AdhocRunResultChunk.chunk_index, AdhocRunResultChunk.id)
        .all()
    )
    for chunk in chunks:
        for raw_row in list((chunk.payload or {}).get("rows") or []):
            scanned += 1
            row = list(raw_row)
            value = row[col_index] if col_index < len(row) else None
            if value is None:
                key = "\x00null"
                token: Any = None
            else:
                key = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
                token = value
            if key in seen:
                continue
            if len(seen) >= max_values:
                truncated = True
                break
            seen[key] = token
        if truncated:
            break

    def sort_key(item: Any) -> tuple:
        if item is None:
            return (0, "")
        return (1, str(item))

    values = sorted(seen.values(), key=sort_key)
    return {
        "statement_id": statement.id,
        "statement_index": statement.statement_index,
        "column": column,
        "values": values,
        "truncated": truncated,
        "scanned_rows": scanned,
        "statement_version": version,
        "version_upgraded": bool(requested and requested != version),
        "statement_status": statement.status,
    }


def paginate_statement_result_chunks(
    db: Session,
    run_id: int,
    statement_index: int,
    *,
    after_cursor: int = -1,
    limit: int = 100,
) -> Dict[str, Any]:
    statement = (
        db.query(AdhocRunStatement)
        .filter(
            AdhocRunStatement.run_id == run_id,
            AdhocRunStatement.statement_index == statement_index,
        )
        .first()
    )
    if not statement:
        raise ValueError("语句不存在")
    page_size = min(max(int(limit), 1), 1000)
    chunks = (
        db.query(AdhocRunResultChunk)
        .filter(
            AdhocRunResultChunk.statement_id == statement.id,
            AdhocRunResultChunk.chunk_index > int(after_cursor),
        )
        .order_by(AdhocRunResultChunk.chunk_index, AdhocRunResultChunk.id)
        .limit(page_size + 1)
        .all()
    )
    has_more = len(chunks) > page_size
    chunks = chunks[:page_size]
    return {
        "statement_id": statement.id,
        "statement_index": statement.statement_index,
        "chunks": chunks,
        "next_cursor": chunks[-1].chunk_index if chunks else int(after_cursor),
        "has_more": has_more,
    }


def build_result_preview_from_statements(
    db: Session, run_id: int, *, max_rows: int = ADHOC_RESULT_PREVIEW_ROWS
) -> Optional[Dict[str, Any]]:
    """Reconstruct the legacy result contract from durable statements."""
    statements = list_run_statements(db, run_id)
    if not statements:
        return None
    blocks: List[Dict[str, Any]] = []
    for statement in statements:
        schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
        columns = list(schema.get("columns") or [])
        rows: List[List[Any]] = []
        if columns:
            chunks = (
                db.query(AdhocRunResultChunk)
                .filter(AdhocRunResultChunk.statement_id == statement.id)
                .order_by(AdhocRunResultChunk.chunk_index, AdhocRunResultChunk.id)
                .all()
            )
            for chunk in chunks:
                rows.extend(list((chunk.payload or {}).get("rows") or []))
                if len(rows) >= max_rows:
                    break
        if columns or statement.status in ("failed", "skipped"):
            blocks.append(
                {
                    "index": statement.statement_index,
                    "sql": statement.sql_text,
                    "status": statement.status,
                    "columns": columns,
                    "column_types": list(schema.get("column_types") or []),
                    "fields": list(schema.get("fields") or _legacy_fields(schema)),
                    "rows": rows[:max_rows],
                    "total": int(statement.result_rows or 0),
                    "truncated": bool(schema.get("truncated"))
                    or int(statement.result_rows or 0) > max_rows,
                    "error": statement.error_message
                    if statement.status in ("failed", "skipped")
                    else None,
                    **(
                        {"affected_rows": int(statement.affected_rows or 0)}
                        if statement.affected_rows is not None
                        else {}
                    ),
                }
            )
    last_ok = next(
        (item for item in reversed(blocks) if not item.get("error") and item.get("columns")),
        None,
    )
    top = dict(
        last_ok
        or {
            "columns": [],
            "column_types": [],
            "fields": [],
            "rows": [],
            "total": 0,
            "truncated": False,
        }
    )
    top.update(
        statement_count=len(statements),
        result_set_count=sum(1 for item in blocks if item.get("columns")),
        statements=blocks,
        has_errors=any(item.status == "failed" for item in statements),
        partial_success=any(item.status == "success" for item in statements)
        and any(item.status == "failed" for item in statements),
    )
    return top


def _sql_content_lines(sql: Optional[str]) -> list[str]:
    """去掉纯空行与整行注释后的有效行（保留行内代码，不含仅 -- 注释行）。"""
    if not sql or not str(sql).strip():
        return []
    out: list[str] = []
    for raw in str(sql).splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            continue
        out.append(raw.rstrip())
    return out


def summarize_sql(sql: Optional[str], max_len: int = SQL_SUMMARY_MAX_LEN) -> Optional[str]:
    """
    列表用 SQL 摘要：首条有效脚本行（跳过空行/整行注释），再截断长度。
    运行历史要一眼看出「执行了什么」，首行比语义动词·表名更直观。
    """
    lines = _sql_content_lines(sql)
    if not lines:
        return None
    first = lines[0].strip()
    if len(first) > max_len:
        return first[: max_len - 1] + "…"
    return first


def preview_sql(
    sql: Optional[str],
    *,
    max_lines: int = SQL_PREVIEW_MAX_LINES,
    max_chars: int = SQL_PREVIEW_MAX_CHARS,
) -> Optional[str]:
    """
    列表 hover 用多行预览：保留原文换行，限制行数与字符数，不返回整段 sql_text。
    """
    if not sql or not str(sql).strip():
        return None
    raw_lines = str(sql).splitlines()
    clipped = raw_lines[: max(1, max_lines)]
    text = "\n".join(clipped)
    truncated = len(raw_lines) > max_lines
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
        truncated = True
    elif truncated:
        text = text + "\n…"
    return text


def truncate_result_preview(result: Optional[Dict[str, Any]], max_rows: int = ADHOC_RESULT_PREVIEW_ROWS) -> Optional[Dict[str, Any]]:
    """将 SQL 结果截断为可入库的预览结构。"""
    if not result or not isinstance(result, dict):
        return None

    def truncate_one(item: Dict[str, Any]) -> Dict[str, Any]:
        columns = list(item.get("columns") or [])
        column_types = list(item.get("column_types") or [])
        fields = list(item.get("fields") or _legacy_fields(item))
        rows = list(item.get("rows") or [])
        total = int(item.get("total") if item.get("total") is not None else len(rows))
        preview = {
            "columns": columns,
            "column_types": column_types,
            "fields": fields,
            "rows": rows[:max_rows],
            "total": total,
            "truncated": bool(item.get("truncated")) or len(rows) > max_rows,
        }
        for key in ("index", "sql", "error", "affected_rows", "status"):
            if key in item:
                preview[key] = item.get(key)
        return preview

    preview = truncate_one(result)
    statements = result.get("statements")
    if isinstance(statements, list):
        preview["statements"] = [
            truncate_one(item) for item in statements if isinstance(item, dict)
        ]
        preview["statement_count"] = int(
            result.get("statement_count")
            if result.get("statement_count") is not None
            else len(statements)
        )
        preview["result_set_count"] = int(
            result.get("result_set_count")
            if result.get("result_set_count") is not None
            else len(preview["statements"])
        )
        for key in ("has_errors", "partial_success"):
            if key in result:
                preview[key] = bool(result.get(key))
    return preview


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
        "run_type": getattr(row, "run_type", None),
        "business_date": getattr(row, "business_date", None),
        "sql_summary": summarize_sql(row.sql_text),
        "sql_preview": preview_sql(row.sql_text),
        "status": row.status,
        "error_message": row.error_message,
        "log_content": row.log_content,
        "rows_returned": row.rows_returned or 0,
        "duration_ms": row.duration_ms,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "heartbeat_at": getattr(row, "heartbeat_at", None),
        "cancel_requested_at": getattr(row, "cancel_requested_at", None),
        "created_at": row.created_at,
        "result_truncated": bool((row.result_preview or {}).get("truncated")) if isinstance(row.result_preview, dict) else False,
    }
    if include_sql:
        data["sql_text"] = row.sql_text
    if include_result:
        data["result_preview"] = row.result_preview
    return data

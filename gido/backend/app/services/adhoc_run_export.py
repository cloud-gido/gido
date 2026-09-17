# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Asynchronous exports built only from immutable adhoc result chunks."""
from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.workspace import (
    AdhocRun,
    AdhocRunExport,
    AdhocRunStatement,
)
from app.services import artifact_s3
from app.services.adhoc_run_store import query_statement_rows, statement_snapshot_version

logger = logging.getLogger(__name__)
EXPORT_NAMESPACE = "adhoc-exports"
TERMINAL_STATEMENT_STATUSES = ("success", "failed", "skipped")

_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()


class ExportCancelled(InterruptedError):
    pass


class ExportTooLarge(ValueError):
    pass


def serialize_export(row: AdhocRunExport) -> dict:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "statement_id": row.statement_id,
        "requested_by": row.requested_by,
        "format": row.format,
        "status": row.status,
        "snapshot_scope": row.snapshot_scope,
        "query_spec": row.query_spec if isinstance(row.query_spec, dict) else {},
        "statement_version": row.statement_version,
        "row_count": int(row.row_count or 0),
        "size_bytes": int(row.size_bytes or 0),
        "file_name": row.file_name,
        "error_message": row.error_message,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "finished_at": row.finished_at,
        "download_ready": row.status == "success",
    }


def create_export(
    db: Session,
    *,
    run: AdhocRun,
    statement: AdhocRunStatement,
    requested_by: int,
    export_format: str,
    query_spec: Optional[Dict[str, Any]] = None,
    statement_version: Optional[str] = None,
) -> AdhocRunExport:
    fmt = str(export_format or "").strip().lower()
    if fmt not in ("csv", "jsonl", "xlsx", "parquet"):
        raise ValueError("导出格式仅支持 csv、jsonl、xlsx 或 parquet")
    if statement.run_id != run.id:
        raise ValueError("语句不属于该运行")
    if statement.status not in TERMINAL_STATEMENT_STATUSES:
        raise ValueError("仅可导出已物化完成的不可变语句快照")
    schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
    if not list(schema.get("columns") or []):
        raise ValueError("该语句没有可导出的结果集")
    version = statement_snapshot_version(statement)
    if statement_version is not None and statement_version != version:
        raise ValueError("语句版本已变化，请刷新结果后重试")
    spec = dict(query_spec or {})
    # Validate the complete persisted query contract before queueing the job.
    query_statement_rows(
        db,
        run.id,
        statement.statement_index,
        search=spec.get("search"),
        filters=list(spec.get("filters") or []),
        sort=list(spec.get("sort") or []),
        limit=1,
        statement_version=version,
    )
    key = uuid.uuid4().hex
    row = AdhocRunExport(
        run_id=run.id,
        statement_id=statement.id,
        requested_by=requested_by,
        export_key=key,
        format=fmt,
        status="queued",
        snapshot_scope=f"statement:{statement.statement_index}",
        query_spec={
            "search": spec.get("search"),
            "filters": list(spec.get("filters") or []),
            "sort": list(spec.get("sort") or []),
        },
        statement_version=version,
        row_count=int(statement.result_rows or 0),
        file_name=f"run-{run.id}-statement-{statement.statement_index}-{key[:8]}.{fmt}",
        expires_at=datetime.utcnow()
        + timedelta(hours=max(1, int(settings.ADHOC_EXPORT_TTL_HOURS))),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _is_cancel_requested(db: Session, export_id: int) -> bool:
    db.expire_all()
    status = (
        db.query(AdhocRunExport.status)
        .filter(AdhocRunExport.id == export_id)
        .scalar()
    )
    return status in ("cancel_requested", "cancelled")


def _iter_export_batches(
    db: Session,
    export: AdhocRunExport,
    statement: AdhocRunStatement,
) -> Iterator[Tuple[List[str], List[list]]]:
    spec = export.query_spec if isinstance(export.query_spec, dict) else {}
    cursor: Optional[str] = None
    while True:
        if _is_cancel_requested(db, export.id):
            raise ExportCancelled("导出已取消")
        page = query_statement_rows(
            db,
            export.run_id,
            statement.statement_index,
            search=spec.get("search"),
            filters=list(spec.get("filters") or []),
            sort=list(spec.get("sort") or []),
            cursor=cursor,
            limit=5000,
            statement_version=str(export.statement_version or ""),
        )
        yield [str(item) for item in page["columns"]], list(page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            return


def _encoded_lines(columns: List[str], rows: Iterator[list], export_format: str) -> Iterator[bytes]:
    if export_format == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(columns)
        yield buffer.getvalue().encode("utf-8-sig")
        for row in rows:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(row)
            yield buffer.getvalue().encode("utf-8")
        return
    for row in rows:
        item = {
            str(columns[index]): (row[index] if index < len(row) else None)
            for index in range(len(columns))
        }
        yield (
            json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\n"
        ).encode("utf-8")


def _write_export_file(
    db: Session,
    export: AdhocRunExport,
    statement: AdhocRunStatement,
    path: str,
) -> Tuple[int, int]:
    row_count = 0
    batches = _iter_export_batches(db, export, statement)
    if export.format in ("csv", "jsonl"):
        with open(path, "wb") as output:
            wrote_header = False
            for columns, rows in batches:
                if export.format == "csv" and wrote_header:
                    lines = _encoded_lines(columns, iter(rows), "csv")
                    next(lines, None)
                else:
                    lines = _encoded_lines(columns, iter(rows), export.format)
                for data in lines:
                    output.write(data)
                wrote_header = True
                row_count += len(rows)
    elif export.format == "xlsx":
        from openpyxl import Workbook

        def xlsx_cell(value: Any) -> Any:
            if isinstance(value, (dict, list, tuple)):
                return json.dumps(value, ensure_ascii=False, default=str)
            if isinstance(value, bytes):
                return value.hex()
            return value

        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("Result")
        wrote_header = False
        for columns, rows in batches:
            if not wrote_header:
                sheet.append(columns)
                wrote_header = True
            for row in rows:
                row_count += 1
                if row_count > 1_048_575:
                    raise ExportTooLarge("XLSX 最多支持 1048575 行数据")
                sheet.append([xlsx_cell(value) for value in row])
        workbook.save(path)
    else:
        import pyarrow as pa
        import pyarrow.parquet as pq

        column_schema = (
            statement.column_schema if isinstance(statement.column_schema, dict) else {}
        )
        field_map = {
            str(item.get("name")): item
            for item in list(column_schema.get("fields") or [])
            if isinstance(item, dict)
        }

        def arrow_type(column: str):
            field = field_map.get(column, {})
            semantic = str(field.get("semantic_type") or "string")
            raw_type = str(field.get("raw_type") or "").lower()
            if semantic == "boolean":
                return pa.bool_()
            if semantic == "number":
                if "int" in raw_type:
                    return pa.int64()
                return pa.float64()
            if semantic == "binary":
                return pa.binary()
            return pa.string()

        def parquet_cell(value: Any, data_type):
            if value is None:
                return None
            if pa.types.is_string(data_type):
                if isinstance(value, (dict, list, tuple)):
                    return json.dumps(value, ensure_ascii=False, default=str)
                return str(value)
            if pa.types.is_boolean(data_type):
                return bool(value)
            if pa.types.is_integer(data_type):
                return int(value)
            if pa.types.is_floating(data_type):
                return float(value)
            if pa.types.is_binary(data_type) and not isinstance(value, bytes):
                text_value = str(value)
                if text_value.startswith("base64:"):
                    return base64.b64decode(text_value[7:])
                return text_value.encode("utf-8")
            return value

        writer = None
        arrow_schema = None
        try:
            for columns, rows in batches:
                if arrow_schema is None:
                    arrow_schema = pa.schema(
                        [pa.field(column, arrow_type(column)) for column in columns]
                    )
                records = [
                    {
                        column: parquet_cell(
                            row[index] if index < len(row) else None,
                            arrow_schema.field(column).type,
                        )
                        for index, column in enumerate(columns)
                    }
                    for row in rows
                ]
                table = pa.Table.from_pylist(records, schema=arrow_schema)
                if writer is None:
                    writer = pq.ParquetWriter(path, table.schema)
                if table.num_rows:
                    writer.write_table(table, row_group_size=min(5000, table.num_rows))
                row_count += len(rows)
        finally:
            if writer is not None:
                writer.close()
        if writer is None:
            raise ValueError("导出结果缺少列定义")
    return os.path.getsize(path), row_count


def process_export(export_id: int) -> None:
    db = SessionLocal()
    temp_path: Optional[str] = None
    try:
        claimed = (
            db.query(AdhocRunExport)
            .filter(
                AdhocRunExport.id == export_id,
                AdhocRunExport.status == "queued",
            )
            .update(
                {
                    AdhocRunExport.status: "running",
                    AdhocRunExport.started_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if claimed != 1:
            return
        export = db.query(AdhocRunExport).filter(AdhocRunExport.id == export_id).one()
        statement = (
            db.query(AdhocRunStatement)
            .filter(AdhocRunStatement.id == export.statement_id)
            .one_or_none()
        )
        if not statement or statement.status not in TERMINAL_STATEMENT_STATUSES:
            raise ValueError("导出快照已不存在或仍可变")
        if export.statement_version != statement_snapshot_version(statement):
            raise ValueError("语句版本已变化，导出快照失效")
        s3_enabled = artifact_s3.artifact_s3_enabled()
        max_bytes = max(1, int(settings.ADHOC_EXPORT_DB_FALLBACK_MAX_BYTES))
        fd, temp_path = tempfile.mkstemp(prefix="gido-adhoc-export-", suffix=f".{export.format}")
        os.close(fd)
        size, row_count = _write_export_file(db, export, statement, temp_path)
        if not s3_enabled and size > max_bytes:
            raise ExportTooLarge(
                f"导出大小超过数据库回退上限 {max_bytes} 字节；请配置 S3 后重试"
            )
        if _is_cancel_requested(db, export.id):
            raise ExportCancelled("导出已取消")
        storage_key = None
        if s3_enabled:
            artifact_s3.put_shared_object_file(
                EXPORT_NAMESPACE,
                export.file_name,
                temp_path,
                content_type=export_content_type(export.format),
            )
            storage_key = export.file_name
        completed = (
            db.query(AdhocRunExport)
            .filter(
                AdhocRunExport.id == export.id,
                AdhocRunExport.status == "running",
            )
            .update(
                {
                    AdhocRunExport.status: "success",
                    AdhocRunExport.size_bytes: size,
                    AdhocRunExport.row_count: row_count,
                    AdhocRunExport.storage_key: storage_key,
                    AdhocRunExport.finished_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        if completed != 1:
            db.rollback()
            if storage_key:
                try:
                    artifact_s3.delete_shared_object(EXPORT_NAMESPACE, storage_key)
                except Exception:
                    logger.warning(
                        "cancelled adhoc export cleanup failed key=%s",
                        storage_key,
                        exc_info=True,
                    )
            raise ExportCancelled("导出已取消")
        db.commit()
    except ExportCancelled:
        db.rollback()
        row = db.query(AdhocRunExport).filter(AdhocRunExport.id == export_id).first()
        if row:
            row.status = "cancelled"
            row.finished_at = datetime.utcnow()
            row.error_message = "导出已取消"
            db.commit()
    except Exception as exc:
        db.rollback()
        row = db.query(AdhocRunExport).filter(AdhocRunExport.id == export_id).first()
        if row:
            row.status = "failed"
            row.finished_at = datetime.utcnow()
            row.error_message = str(exc)[:4000]
            db.commit()
        logger.warning("adhoc export failed id=%s", export_id, exc_info=True)
    finally:
        db.close()
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def cancel_export(db: Session, row: AdhocRunExport) -> AdhocRunExport:
    if row.status == "queued":
        db.query(AdhocRunExport).filter(
            AdhocRunExport.id == row.id,
            AdhocRunExport.status == "queued",
        ).update(
            {
                AdhocRunExport.status: "cancelled",
                AdhocRunExport.finished_at: datetime.utcnow(),
                AdhocRunExport.error_message: "导出已取消",
            },
            synchronize_session=False,
        )
    elif row.status == "running":
        db.query(AdhocRunExport).filter(
            AdhocRunExport.id == row.id,
            AdhocRunExport.status == "running",
        ).update(
            {AdhocRunExport.status: "cancel_requested"},
            synchronize_session=False,
        )
    db.commit()
    db.expire_all()
    row = db.query(AdhocRunExport).filter(AdhocRunExport.id == row.id).one()
    db.refresh(row)
    return row


def export_content_type(export_format: str) -> str:
    return {
        "csv": "text/csv; charset=utf-8",
        "jsonl": "application/x-ndjson; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "parquet": "application/vnd.apache.parquet",
    }[export_format]


def stream_database_export(export_id: int) -> Iterator[bytes]:
    db = SessionLocal()
    temp_path: Optional[str] = None
    try:
        export = db.query(AdhocRunExport).filter(AdhocRunExport.id == export_id).one()
        statement = (
            db.query(AdhocRunStatement)
            .filter(AdhocRunStatement.id == export.statement_id)
            .one()
        )
        fd, temp_path = tempfile.mkstemp(
            prefix="gido-adhoc-download-", suffix=f".{export.format}"
        )
        os.close(fd)
        _write_export_file(db, export, statement, temp_path)
        with open(temp_path, "rb") as handle:
            while True:
                data = handle.read(1024 * 1024)
                if not data:
                    break
                yield data
    finally:
        db.close()
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def stream_s3_export(storage_key: str) -> Iterator[bytes]:
    yield from artifact_s3.iter_shared_object(EXPORT_NAMESPACE, storage_key)


def expire_exports() -> int:
    db = SessionLocal()
    expired = 0
    try:
        rows = (
            db.query(AdhocRunExport)
            .filter(
                AdhocRunExport.expires_at <= datetime.utcnow(),
                AdhocRunExport.status.in_(("queued", "success", "failed", "cancelled")),
            )
            .limit(100)
            .all()
        )
        for row in rows:
            if row.storage_key:
                artifact_s3.delete_shared_object(EXPORT_NAMESPACE, row.storage_key)
            row.status = "expired"
            row.storage_key = None
            expired += 1
        db.commit()
        return expired
    finally:
        db.close()


def _dispatcher_loop() -> None:
    interval = max(0.1, float(settings.ADHOC_EXPORT_POLL_SECONDS))
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            export_row = (
                db.query(AdhocRunExport.id)
                .filter(AdhocRunExport.status == "queued")
                .order_by(AdhocRunExport.created_at, AdhocRunExport.id)
                .first()
            )
            export_id = export_row[0] if export_row else None
        except Exception:
            logger.debug("adhoc export poll failed", exc_info=True)
            export_id = None
        finally:
            db.close()
        if export_id is not None:
            process_export(int(export_id))
        else:
            try:
                expire_exports()
            except Exception:
                logger.debug("adhoc export expiry failed", exc_info=True)
            _stop_event.wait(interval)


def start_export_dispatcher() -> None:
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(
            target=_dispatcher_loop, name="adhoc-export-dispatcher", daemon=True
        )
        _thread.start()


def stop_export_dispatcher() -> None:
    global _thread
    _stop_event.set()
    with _thread_lock:
        thread = _thread
    if thread and thread.is_alive():
        thread.join(timeout=5)
    with _thread_lock:
        # Keep a still-running thread registered so start() cannot clear its
        # stop event and create a second dispatcher over the same queue.
        if _thread is thread and (thread is None or not thread.is_alive()):
            _thread = None

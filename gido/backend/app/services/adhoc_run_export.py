# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Asynchronous exports built only from immutable adhoc result chunks."""
from __future__ import annotations

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
from typing import Iterator, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.workspace import (
    AdhocRun,
    AdhocRunExport,
    AdhocRunResultChunk,
    AdhocRunStatement,
)
from app.services import artifact_s3

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
) -> AdhocRunExport:
    fmt = str(export_format or "").strip().lower()
    if fmt not in ("csv", "jsonl"):
        raise ValueError("导出格式仅支持 csv 或 jsonl")
    if statement.run_id != run.id:
        raise ValueError("语句不属于该运行")
    if statement.status not in TERMINAL_STATEMENT_STATUSES:
        raise ValueError("仅可导出已物化完成的不可变语句快照")
    schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
    if not list(schema.get("columns") or []):
        raise ValueError("该语句没有可导出的结果集")
    key = uuid.uuid4().hex
    suffix = "csv" if fmt == "csv" else "jsonl"
    row = AdhocRunExport(
        run_id=run.id,
        statement_id=statement.id,
        requested_by=requested_by,
        export_key=key,
        format=fmt,
        status="queued",
        snapshot_scope=f"statement:{statement.statement_index}",
        row_count=int(statement.result_rows or 0),
        file_name=f"run-{run.id}-statement-{statement.statement_index}-{key[:8]}.{suffix}",
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


def _iter_statement_rows(
    db: Session, statement_id: int, export_id: Optional[int] = None
) -> Iterator[list]:
    chunks = (
        db.query(AdhocRunResultChunk)
        .filter(AdhocRunResultChunk.statement_id == statement_id)
        .order_by(AdhocRunResultChunk.chunk_index, AdhocRunResultChunk.id)
        .yield_per(50)
    )
    for chunk in chunks:
        if export_id is not None and _is_cancel_requested(db, export_id):
            raise ExportCancelled("导出已取消")
        for row in list((chunk.payload or {}).get("rows") or []):
            yield list(row)


def _encoded_lines(
    columns: list[str], rows: Iterator[list], export_format: str
) -> Iterator[bytes]:
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
        schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
        columns = [str(item) for item in list(schema.get("columns") or [])]
        s3_enabled = artifact_s3.artifact_s3_enabled()
        max_bytes = max(1, int(settings.ADHOC_EXPORT_DB_FALLBACK_MAX_BYTES))
        fd, temp_path = tempfile.mkstemp(prefix="gido-adhoc-export-", suffix=f".{export.format}")
        os.close(fd)
        size = 0
        with open(temp_path, "wb") as output:
            rows = _iter_statement_rows(db, statement.id, export.id)
            for data in _encoded_lines(columns, rows, export.format):
                size += len(data)
                if not s3_enabled and size > max_bytes:
                    raise ExportTooLarge(
                        f"导出大小超过数据库回退上限 {max_bytes} 字节；请配置 S3 后重试"
                    )
                output.write(data)
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
    return (
        "text/csv; charset=utf-8"
        if export_format == "csv"
        else "application/x-ndjson; charset=utf-8"
    )


def stream_database_export(export_id: int) -> Iterator[bytes]:
    db = SessionLocal()
    try:
        export = db.query(AdhocRunExport).filter(AdhocRunExport.id == export_id).one()
        statement = (
            db.query(AdhocRunStatement)
            .filter(AdhocRunStatement.id == export.statement_id)
            .one()
        )
        schema = statement.column_schema if isinstance(statement.column_schema, dict) else {}
        columns = [str(item) for item in list(schema.get("columns") or [])]
        yield from _encoded_lines(
            columns, _iter_statement_rows(db, statement.id), export.format
        )
    finally:
        db.close()


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

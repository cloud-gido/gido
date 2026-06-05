# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据集成：表同步执行（全量 / 增量）、异步运行、水位持久化。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.workspace import DataSource, SyncRecord, SyncTask
from app.services.integration_runtime import (
    assert_supported_ds,
    list_columns,
    open_connection,
    quote_ident,
)

logger = logging.getLogger(__name__)

_running_tasks: set[int] = set()
_run_lock = threading.Lock()

MAX_ROWS_PER_RUN = 500_000


def _cfg(task: SyncTask) -> Dict[str, Any]:
    c = task.sync_config
    return c if isinstance(c, dict) else {}


def _field_mappings(cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = cfg.get("field_mappings") or []
    out: List[Dict[str, str]] = []
    for m in raw:
        if isinstance(m, dict) and m.get("src") and m.get("dst"):
            out.append({"src": str(m["src"]), "dst": str(m["dst"])})
    return out


def _execute_sync(task: SyncTask, src_ds: DataSource, dst_ds: DataSource) -> Tuple[int, int, Optional[str]]:
    """返回 rows_read, rows_written, new_incremental_value"""
    src_lt = assert_supported_ds(src_ds, "源")
    dst_lt = assert_supported_ds(dst_ds, "目标")
    cfg = _cfg(task)
    mappings = _field_mappings(cfg)
    where_clause = (cfg.get("where_clause") or "").strip()
    batch_size = max(100, min(int(cfg.get("batch_size") or 2000), 10000))
    pre_sql = (cfg.get("pre_sql") or "").strip()
    post_sql = (cfg.get("post_sql") or "").strip()

    src_table = task.src_table
    dst_table = task.dst_table
    if not src_table or not dst_table:
        raise ValueError("源表或目标表未配置")

    new_watermark: Optional[str] = None
    rows_read = 0
    rows_written = 0

    with open_connection(src_ds) as src_opened, open_connection(dst_ds) as dst_opened:
        src_kind = src_opened[0]
        dst_kind = dst_opened[0]
        src_conn = src_opened[1]
        src_cur = src_conn.cursor()
        dst_conn = dst_opened[1]
        dst_cur = dst_conn.cursor()
        # 连接协议：mysql 含 Doris；postgresql 才有 schema
        dst_schema = dst_opened[2] if dst_kind == "postgresql" else None

        if pre_sql:
            dst_cur.execute(pre_sql)
            dst_conn.commit()

        if mappings:
            src_cols_sql = ", ".join(quote_ident(src_lt, m["src"]) for m in mappings)
            dst_cols_sql = ", ".join(quote_ident(dst_lt, m["dst"]) for m in mappings)
            src_col_names = [m["src"] for m in mappings]
            dst_col_names = [m["dst"] for m in mappings]
        else:
            src_cols_sql = "*"
            dst_cols_sql = None
            src_col_names = []
            dst_col_names = []

        where_parts: List[str] = []
        if task.sync_mode == "incremental":
            inc_col = (cfg.get("incremental_column") or "").strip()
            if not inc_col:
                raise ValueError("增量模式须配置 incremental_column")
            last_val = cfg.get("last_value")
            if last_val is None or last_val == "":
                last_val = cfg.get("incremental_start") or "1970-01-01 00:00:00"
            if src_lt == "mysql":
                where_parts.append(f"{quote_ident(src_lt, inc_col)} > %s")
            else:
                where_parts.append(f"{quote_ident(src_lt, inc_col)} > %s")
            inc_param = last_val
        else:
            inc_param = None

        if where_clause:
            where_parts.append(f"({where_clause})")

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        src_q = f"SELECT {src_cols_sql} FROM {quote_ident(src_lt, src_table)}{where_sql}"
        if inc_param is not None:
            src_cur.execute(src_q, (inc_param,))
        else:
            src_cur.execute(src_q)

        if not dst_cols_sql:
            dst_cols_sql = ", ".join(quote_ident(dst_lt, d[0]) for d in src_cur.description)
            src_col_names = [d[0] for d in src_cur.description]
            dst_col_names = list(src_col_names)

        if task.sync_mode == "full":
            if dst_kind == "mysql":
                dst_cur.execute(f"TRUNCATE TABLE {quote_ident(dst_lt, dst_table)}")
            else:
                if dst_schema and dst_schema != "public":
                    dst_cur.execute(
                        f"TRUNCATE TABLE {quote_ident(dst_lt, dst_schema)}.{quote_ident(dst_lt, dst_table)}"
                    )
                else:
                    dst_cur.execute(f"TRUNCATE TABLE {quote_ident(dst_lt, dst_table)}")
            dst_conn.commit()

        placeholders = ", ".join(["%s"] * len(dst_col_names))
        dst_col_list = dst_cols_sql

        dst_table_ref = quote_ident(dst_lt, dst_table)
        if dst_kind == "mysql":
            # Doris 走 MySQL 协议但不支持 ON DUPLICATE KEY UPDATE；Unique Key 表普通 INSERT 即 upsert
            if dst_lt == "doris":
                insert_sql = (
                    f"INSERT INTO {dst_table_ref} ({dst_col_list}) VALUES ({placeholders})"
                )
            else:
                upsert = ", ".join(
                    f"{quote_ident(dst_lt, c)}=VALUES({quote_ident(dst_lt, c)})" for c in dst_col_names
                )
                insert_sql = (
                    f"INSERT INTO {dst_table_ref} ({dst_col_list}) "
                    f"VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {upsert}"
                )
        else:
            pk_cols = _pg_primary_keys(dst_cur, dst_schema or "public", dst_table)
            if pk_cols:
                conflict = ", ".join(
                    f"{quote_ident(dst_lt, c)}=EXCLUDED.{quote_ident(dst_lt, c)}"
                    for c in dst_col_names
                    if c not in pk_cols
                ) or f"{quote_ident(dst_lt, dst_col_names[0])}=EXCLUDED.{quote_ident(dst_lt, dst_col_names[0])}"
                insert_sql = (
                    f"INSERT INTO {dst_table_ref} ({dst_col_list}) "
                    f"VALUES ({placeholders}) ON CONFLICT ({', '.join(quote_ident(dst_lt, c) for c in pk_cols)}) "
                    f"DO UPDATE SET {conflict}"
                )
            else:
                insert_sql = f"INSERT INTO {dst_table_ref} ({dst_col_list}) VALUES ({placeholders})"

        inc_col_name = (cfg.get("incremental_column") or "").strip() if task.sync_mode == "incremental" else ""
        inc_idx = src_col_names.index(inc_col_name) if inc_col_name and inc_col_name in src_col_names else -1

        while True:
            batch = src_cur.fetchmany(batch_size)
            if not batch:
                break
            rows_read += len(batch)
            if rows_read > MAX_ROWS_PER_RUN:
                raise ValueError(f"单次同步超过上限 {MAX_ROWS_PER_RUN} 行，请缩小范围或提高 batch/过滤条件")

            dst_cur.executemany(insert_sql, batch)
            dst_conn.commit()
            rows_written += len(batch)

            if inc_idx >= 0 and batch:
                vals = [str(row[inc_idx]) for row in batch if row[inc_idx] is not None]
                if vals:
                    new_watermark = max(vals)

        if post_sql:
            dst_cur.execute(post_sql)
            dst_conn.commit()

    return rows_read, rows_written, new_watermark


def _pg_primary_keys(cur, schema: str, table: str) -> List[str]:
    cur.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = %s AND kcu.table_name = %s
        ORDER BY kcu.ordinal_position
        """,
        (schema, table),
    )
    return [r[0] for r in cur.fetchall()]


def run_sync_record(record_id: int, task_id: int) -> None:
    db = SessionLocal()
    try:
        record = db.query(SyncRecord).filter(SyncRecord.id == record_id).first()
        task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
        if not record or not task:
            return
        src_ds = db.query(DataSource).filter(DataSource.id == task.src_datasource_id).first()
        dst_ds = db.query(DataSource).filter(DataSource.id == task.dst_datasource_id).first()
        if not src_ds or not dst_ds:
            record.status = "failed"
            record.error_msg = "数据源不存在"
            record.finished_at = datetime.utcnow()
            db.commit()
            return

        try:
            saved_mode = task.sync_mode
            if saved_mode == "cdc":
                task.sync_mode = "incremental"
            rows_read, rows_written, wm = _execute_sync(task, src_ds, dst_ds)
            task.sync_mode = saved_mode
            record.status = "success"
            record.rows_read = rows_read
            record.rows_written = rows_written
            task.last_sync_at = datetime.utcnow()
            task.last_run_status = "success"
            cfg = _cfg(task)
            if wm is not None and saved_mode in ("incremental", "cdc"):
                cfg["last_value"] = wm
                task.sync_config = cfg
        except Exception as e:
            logger.exception("sync task %s failed", task_id)
            record.status = "failed"
            record.error_msg = str(e)[:4000]
            task.last_run_status = "failed"
        finally:
            record.finished_at = datetime.utcnow()
            if record.started_at:
                delta = record.finished_at - record.started_at
                record.duration_ms = int(delta.total_seconds() * 1000)
            db.commit()
    finally:
        db.close()
        with _run_lock:
            _running_tasks.discard(task_id)


def start_sync_async(task_id: int, trigger_type: str = "manual") -> SyncRecord:
    with _run_lock:
        if task_id in _running_tasks:
            raise RuntimeError("该任务正在执行中，请稍后再试")
        _running_tasks.add(task_id)

    db = SessionLocal()
    try:
        task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
        if not task:
            raise ValueError("任务不存在")
        if not task.is_active:
            raise ValueError("任务已停用，无法执行")
        record = SyncRecord(
            sync_task_id=task_id,
            status="running",
            trigger_type=trigger_type,
            started_at=datetime.utcnow(),
        )
        db.add(record)
        task.last_run_status = "running"
        db.commit()
        db.refresh(record)
        rid, tid = record.id, task_id
    finally:
        db.close()

    t = threading.Thread(target=run_sync_record, args=(rid, tid), daemon=True, name=f"sync-{tid}")
    t.start()
    return record


def validate_task_config(task: SyncTask, src_ds: DataSource, dst_ds: DataSource) -> List[str]:
    warnings: List[str] = []
    try:
        assert_supported_ds(src_ds, "源")
        assert_supported_ds(dst_ds, "目标")
    except ValueError as e:
        return [str(e)]
    if not task.src_table or not task.dst_table:
        warnings.append("请配置源表与目标表")
    cfg = _cfg(task)
    if task.sync_mode == "incremental" and not (cfg.get("incremental_column") or "").strip():
        warnings.append("增量模式须配置增量字段 incremental_column")
    mappings = _field_mappings(cfg)
    if mappings:
        src_cols = {c["name"] for c in list_columns(src_ds, task.src_table)} if task.src_table else set()
        for m in mappings:
            if m["src"] not in src_cols:
                warnings.append(f"源字段不存在: {m['src']}")
    return warnings

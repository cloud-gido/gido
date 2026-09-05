# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据集成 SyncRecord 持久队列：pending 认领 + 心跳 + 超时回收。"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.workspace import SyncRecord, SyncTask
from app.services.distributed_lock import acquire_distributed_lock

logger = logging.getLogger(__name__)

_worker_thread: Optional[threading.Thread] = None
_running = False


def _heartbeat(record_id: int, phase: Optional[str] = None) -> None:
    db = SessionLocal()
    try:
        rec = db.query(SyncRecord).filter(SyncRecord.id == record_id).first()
        if not rec:
            return
        rec.heartbeat_at = datetime.utcnow()
        if phase:
            rec.phase = phase
        db.commit()
    except Exception:
        logger.exception("sync heartbeat failed record=%s", record_id)
    finally:
        db.close()


def reclaim_stale_running(db=None) -> int:
    """将长时间无心跳的 running 回收为 failed，允许幂等 retry。"""
    own = db is None
    if own:
        db = SessionLocal()
    try:
        mins = int(getattr(settings, "FILE_IMPORT_STALE_RUNNING_MINUTES", 120) or 120)
        cutoff = datetime.utcnow() - timedelta(minutes=max(5, mins))
        rows = (
            db.query(SyncRecord)
            .filter(SyncRecord.status == "running")
            .filter(
                (SyncRecord.heartbeat_at.isnot(None) & (SyncRecord.heartbeat_at < cutoff))
                | (
                    SyncRecord.heartbeat_at.is_(None)
                    & SyncRecord.started_at.isnot(None)
                    & (SyncRecord.started_at < cutoff)
                )
            )
            .all()
        )
        n = 0
        for r in rows:
            r.status = "failed"
            r.phase = "stale"
            r.error_msg = (r.error_msg or "")[:2000] + f"\n[reclaimed] running 超时无心跳（>{mins}m）"
            r.finished_at = datetime.utcnow()
            task = db.query(SyncTask).filter(SyncTask.id == r.sync_task_id).first()
            if task and task.last_run_status == "running":
                task.last_run_status = "failed"
            n += 1
        if n:
            db.commit()
            logger.warning("reclaimed %s stale sync records", n)
        return n
    finally:
        if own:
            db.close()


def enqueue_sync_record(
    task_id: int,
    *,
    trigger_type: str = "manual",
    triggered_by: Optional[int] = None,
    execution_key: Optional[str] = None,
    retry_of: Optional[int] = None,
    version_id: Optional[int] = None,
    config_snapshot: Optional[dict] = None,
) -> SyncRecord:
    """创建 pending 记录；由 worker 认领执行（不再直接起 daemon 线程跑业务）。"""
    db = SessionLocal()
    try:
        task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
        if not task:
            raise ValueError("任务不存在")
        if not task.is_active:
            raise ValueError("任务已停用，无法执行")
        # 同任务已有 pending/running 则拒绝
        busy = (
            db.query(SyncRecord)
            .filter(
                SyncRecord.sync_task_id == task_id,
                SyncRecord.status.in_(("pending", "running")),
            )
            .first()
        )
        if busy:
            raise RuntimeError("该任务正在执行或排队中，请稍后再试")
        key = execution_key or uuid.uuid4().hex
        record = SyncRecord(
            sync_task_id=task_id,
            status="pending",
            trigger_type=trigger_type,
            started_at=None,
            execution_key=key,
            retry_of=retry_of,
            version_id=version_id,
            config_snapshot=config_snapshot,
            phase="queued",
            heartbeat_at=datetime.utcnow(),
            triggered_by=triggered_by,
        )
        db.add(record)
        task.last_run_status = "pending"
        db.commit()
        db.refresh(record)
        return record
    finally:
        db.close()


def _claim_one(db) -> Optional[SyncRecord]:
    reclaim_stale_running(db)
    q = (
        db.query(SyncRecord)
        .filter(SyncRecord.status == "pending")
        .order_by(SyncRecord.id.asc())
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    rec = q.first()
    if not rec:
        return None
    # 分布式锁按任务维度，避免多副本同时跑同一 task
    lock = acquire_distributed_lock(f"integration-sync:{int(rec.sync_task_id)}")
    if lock is None:
        return None
    try:
        rec.status = "running"
        rec.started_at = datetime.utcnow()
        rec.heartbeat_at = datetime.utcnow()
        rec.phase = "claimed"
        task = db.query(SyncTask).filter(SyncTask.id == rec.sync_task_id).first()
        if task:
            task.last_run_status = "running"
        db.commit()
        db.refresh(rec)
        # 在锁持有下同步执行（worker 线程内）
        from app.services.integration_sync import run_sync_record

        run_sync_record(rec.id, rec.sync_task_id, lock, heartbeat_cb=lambda p=None: _heartbeat(rec.id, p))
        return rec
    except Exception:
        try:
            lock.release()
        except Exception:
            pass
        raise


def _worker_loop() -> None:
    logger.info("sync pending worker started")
    while _running:
        db = SessionLocal()
        try:
            claimed = _claim_one(db)
            if not claimed:
                time.sleep(1.0)
        except Exception:
            logger.exception("sync worker loop error")
            time.sleep(2.0)
        finally:
            db.close()


def start_sync_worker() -> None:
    global _worker_thread, _running
    if _worker_thread and _worker_thread.is_alive():
        return
    if getattr(settings, "FILE_IMPORT_REQUIRE_SHARED_STORAGE", False):
        from app.services.file_import_store import file_import_shared_enabled

        if not file_import_shared_enabled():
            logger.error(
                "FILE_IMPORT_REQUIRE_SHARED_STORAGE=true 但未启用 S3+Redis；"
                "多副本文件导入不安全，请配置制品 S3 与 Redis"
            )
    _running = True
    _worker_thread = threading.Thread(target=_worker_loop, name="sync-pending-worker", daemon=True)
    _worker_thread.start()


def stop_sync_worker() -> None:
    global _running
    _running = False

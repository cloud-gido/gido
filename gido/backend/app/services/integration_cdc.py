# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""CDC / 准实时同步：后台按间隔执行增量同步（水位自动推进）。"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.database import SessionLocal
from app.models.workspace import DataSource, SyncRecord, SyncTask
from app.services.integration_sync import _cfg, _execute_sync

logger = logging.getLogger(__name__)

_manager_thread: Optional[threading.Thread] = None
_running = False
_active_workers: Dict[int, threading.Thread] = {}
_workers_lock = threading.Lock()


def _cdc_cfg(task: SyncTask) -> Dict[str, Any]:
    c = _cfg(task)
    block = c.get("cdc")
    return block if isinstance(block, dict) else {}


def _merge_cdc(task: SyncTask, patch: Dict[str, Any]) -> None:
    cfg = _cfg(task)
    cdc = dict(_cdc_cfg(task))
    cdc.update(patch)
    cfg["cdc"] = cdc
    task.sync_config = cfg
    task.updated_at = datetime.utcnow()


def _cdc_worker_loop(task_id: int) -> None:
    logger.info("CDC worker 启动 task_id=%s", task_id)
    while _running:
        db = SessionLocal()
        poll = 10
        try:
            task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
            if not task or task.sync_mode != "cdc" or not task.is_active:
                break
            cdc = _cdc_cfg(task)
            if not cdc.get("running"):
                break
            poll = max(3, int(cdc.get("poll_interval_sec") or 10))

            if not (cdc.get("incremental_column") or (_cfg(task).get("incremental_column"))):
                logger.error("CDC 任务 %s 缺少 incremental_column", task_id)
                time.sleep(poll)
                continue

            src_ds = db.query(DataSource).filter(DataSource.id == task.src_datasource_id).first()
            dst_ds = db.query(DataSource).filter(DataSource.id == task.dst_datasource_id).first()
            if not src_ds or not dst_ds:
                time.sleep(poll)
                continue

            record = SyncRecord(
                sync_task_id=task_id,
                status="running",
                trigger_type="cdc",
                started_at=datetime.utcnow(),
            )
            db.add(record)
            task.last_run_status = "running"
            db.commit()
            db.refresh(record)

            saved_mode = task.sync_mode
            try:
                task.sync_mode = "incremental"
                if cdc.get("use_binlog") and (src_ds.ds_type or "").lower() in ("mysql", "doris"):
                    try:
                        _try_advance_binlog_watermark(task, src_ds, cdc)
                    except Exception as be:
                        logger.info("CDC task %s binlog 降级增量: %s", task_id, be)
                rows_read, rows_written, wm = _execute_sync(task, src_ds, dst_ds)
                record.rows_read = rows_read
                record.rows_written = rows_written
                record.status = "success"
                task.last_run_status = "success"
                task.last_sync_at = datetime.utcnow()
                if wm is not None:
                    cfg = _cfg(task)
                    cfg["last_value"] = wm
                    task.sync_config = cfg
                _merge_cdc(task, {"last_tick": datetime.utcnow().isoformat()})
                db.commit()
            except Exception as e:
                record.status = "failed"
                record.error_msg = str(e)[:4000]
                task.last_run_status = "failed"
                db.commit()
                logger.warning("CDC task %s tick failed: %s", task_id, e)
            finally:
                task.sync_mode = saved_mode
                record.finished_at = datetime.utcnow()
                if record.started_at:
                    record.duration_ms = int((record.finished_at - record.started_at).total_seconds() * 1000)
                db.commit()
        finally:
            db.close()

        time.sleep(poll)

    with _workers_lock:
        _active_workers.pop(task_id, None)
    db2 = SessionLocal()
    try:
        t = db2.query(SyncTask).filter(SyncTask.id == task_id).first()
        if t:
            _merge_cdc(t, {"running": False})
            db2.commit()
    finally:
        db2.close()
    logger.info("CDC worker 结束 task_id=%s", task_id)


def _manager_loop() -> None:
    logger.info("CDC 管理器已启动")
    while _running:
        db = SessionLocal()
        try:
            tasks = db.query(SyncTask).filter(SyncTask.sync_mode == "cdc", SyncTask.is_active.is_(True)).all()
            for t in tasks:
                if not _cdc_cfg(t).get("running"):
                    continue
                with _workers_lock:
                    if t.id in _active_workers and _active_workers[t.id].is_alive():
                        continue
                    th = threading.Thread(target=_cdc_worker_loop, args=(t.id,), daemon=True, name=f"cdc-{t.id}")
                    _active_workers[t.id] = th
                    th.start()
        finally:
            db.close()
        time.sleep(15)


def start_cdc_manager() -> None:
    global _manager_thread, _running
    if _running:
        return
    _running = True
    _manager_thread = threading.Thread(target=_manager_loop, daemon=True, name="integration-cdc-manager")
    _manager_thread.start()


def stop_cdc_manager() -> None:
    global _running
    _running = False


def start_task_cdc(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
        if not task:
            raise ValueError("任务不存在")
        if task.sync_mode != "cdc":
            raise ValueError("仅 sync_mode=cdc 的任务可启动 CDC")
        _merge_cdc(task, {"running": True, "started_at": datetime.utcnow().isoformat()})
        db.commit()
    finally:
        db.close()
    with _workers_lock:
        if task_id not in _active_workers or not _active_workers[task_id].is_alive():
            th = threading.Thread(target=_cdc_worker_loop, args=(task_id,), daemon=True, name=f"cdc-{task_id}")
            _active_workers[task_id] = th
            th.start()


def stop_task_cdc(task_id: int) -> None:
    db = SessionLocal()
    try:
        task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
        if task:
            _merge_cdc(task, {"running": False})
            db.commit()
    finally:
        db.close()


def cdc_status(task: SyncTask) -> Dict[str, Any]:
    cdc = _cdc_cfg(task)
    with _workers_lock:
        alive = task.id in _active_workers and _active_workers[task.id].is_alive()
    return {
        "running": bool(cdc.get("running")),
        "worker_alive": alive,
        "poll_interval_sec": cdc.get("poll_interval_sec") or 10,
        "last_tick": cdc.get("last_tick"),
        "started_at": cdc.get("started_at"),
    }

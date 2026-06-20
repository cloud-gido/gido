# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""实时作业批量启停：任务队列、校验、异步 worker。"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.publish_approval import submit_publish_approval
from app.services.rbac import workspace_data_full_control

logger = logging.getLogger(__name__)

BATCH_ACTION_START = "start"
BATCH_ACTION_CANCEL = "cancel"
VALID_BATCH_ACTIONS = frozenset({BATCH_ACTION_START, BATCH_ACTION_CANCEL})

_running_batch_tasks: set[int] = set()
_run_lock = threading.Lock()


def _job_platform_status_running(status: Optional[str]) -> bool:
    return (status or "").strip().lower() == "running"


def _job_has_runtime(job) -> bool:
    from app.api.streaming import _operator_deployment_name_for_job

    return bool(
        job.flink_job_id
        or _operator_deployment_name_for_job(job)
        or getattr(job, "flink_application_cluster_id", None)
    )


def validate_batch_job_ids(
    jobs: Sequence[Any],
    action: str,
    *,
    max_jobs: int,
) -> Tuple[List[Any], List[str]]:
    """返回 (有效作业列表, 警告/跳过的说明)。action=start 时 running 作业直接报错。"""
    if action not in VALID_BATCH_ACTIONS:
        raise ValueError(f"不支持的批量动作: {action}")
    if len(jobs) > max_jobs:
        raise ValueError(f"单次最多 {max_jobs} 个作业，当前 {len(jobs)} 个")
    if not jobs:
        raise ValueError("请至少选择一个作业")

    ids = [j.id for j in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("job_ids 存在重复")

    if action == BATCH_ACTION_START:
        running = [j for j in jobs if _job_platform_status_running(j.status)]
        if running:
            names = ", ".join(j.name for j in running[:5])
            extra = f" 等 {len(running)} 个" if len(running) > 5 else ""
            raise ValueError(f"批量启动不允许包含 running 状态作业：{names}{extra}")
        return list(jobs), []

    warnings: List[str] = []
    return list(jobs), warnings


def serialize_batch_task(db: Session, task, *, include_items: bool = True) -> Dict[str, Any]:
    from app.api.streaming import StreamingBatchTaskItem

    done = (task.succeeded or 0) + (task.failed or 0) + (task.skipped or 0)
    total = task.total or 0
    percent = int(min(100, round(done * 100 / total))) if total else 0
    out: Dict[str, Any] = {
        "id": task.id,
        "workspace_id": task.workspace_id,
        "action": task.action,
        "status": task.status,
        "total": total,
        "succeeded": task.succeeded or 0,
        "failed": task.failed or 0,
        "skipped": task.skipped or 0,
        "progress_percent": percent,
        "approval_id": getattr(task, "approval_id", None),
        "submit_note": getattr(task, "submit_note", None),
        "require_savepoint": bool(getattr(task, "require_savepoint", True)),
        "created_by": task.created_by,
        "created_at": task.created_at,
        "started_at": getattr(task, "started_at", None),
        "finished_at": getattr(task, "finished_at", None),
    }
    if include_items:
        items = (
            db.query(StreamingBatchTaskItem)
            .filter(StreamingBatchTaskItem.batch_task_id == task.id)
            .order_by(StreamingBatchTaskItem.id)
            .all()
        )
        out["items"] = [serialize_batch_item(it) for it in items]
    return out


def serialize_batch_item(item) -> Dict[str, Any]:
    result = None
    raw = getattr(item, "result_json", None)
    if raw:
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = raw
    return {
        "id": item.id,
        "job_id": item.job_id,
        "job_name": item.job_name,
        "status": item.status,
        "error_message": item.error_message,
        "result": result,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
    }


def _mark_item_skipped(item, reason: str) -> None:
    item.status = "skipped"
    item.error_message = reason[:4000]
    item.finished_at = datetime.utcnow()


def run_batch_task_worker(task_id: int, actor_user_id: int) -> None:
    from app.api.streaming import (
        StreamingBatchTask,
        StreamingBatchTaskItem,
        StreamingJob,
        User,
        execute_streaming_job_cancel,
        execute_streaming_job_submit,
    )

    db = SessionLocal()
    try:
        task = db.query(StreamingBatchTask).filter(StreamingBatchTask.id == task_id).first()
        if not task:
            return
        if task.status not in ("queued", "running"):
            return
        actor = db.query(User).filter(User.id == actor_user_id).first()
        if not actor:
            actor = db.query(User).filter(User.id == task.created_by).first()
        task.status = "running"
        task.started_at = task.started_at or datetime.utcnow()
        db.commit()

        items = (
            db.query(StreamingBatchTaskItem)
            .filter(StreamingBatchTaskItem.batch_task_id == task_id)
            .order_by(StreamingBatchTaskItem.id)
            .all()
        )
        for item in items:
            if item.status == "skipped":
                continue
            item.status = "running"
            item.started_at = datetime.utcnow()
            item.error_message = None
            db.commit()

            job = db.query(StreamingJob).filter(StreamingJob.id == item.job_id).first()
            if not job or int(job.workspace_id or 0) != int(task.workspace_id):
                item.status = "failed"
                item.error_message = "作业不存在或不属于该工作空间"
                item.finished_at = datetime.utcnow()
                task.failed = (task.failed or 0) + 1
                db.commit()
                continue

            try:
                if task.action == BATCH_ACTION_START:
                    if _job_platform_status_running(job.status):
                        raise RuntimeError("作业已为 running，跳过启动")
                    out = execute_streaming_job_submit(db, job, actor, script_content=None)
                else:
                    if not _job_has_runtime(job):
                        _mark_item_skipped(item, "无运行实例，无需停止")
                        task.skipped = (task.skipped or 0) + 1
                        db.commit()
                        continue
                    out = execute_streaming_job_cancel(
                        db,
                        job,
                        require_savepoint=bool(getattr(task, "require_savepoint", True)),
                    )
                item.status = "succeeded"
                item.result_json = json.dumps(out, ensure_ascii=False, default=str)[:32000]
                task.succeeded = (task.succeeded or 0) + 1
            except HTTPException as ex:
                item.status = "failed"
                item.error_message = str(ex.detail)[:4000]
                task.failed = (task.failed or 0) + 1
            except Exception as ex:
                item.status = "failed"
                item.error_message = str(ex)[:4000]
                task.failed = (task.failed or 0) + 1
            finally:
                item.finished_at = datetime.utcnow()
                db.commit()

        task.status = "completed"
        task.finished_at = datetime.utcnow()
        db.commit()
    except Exception:
        logger.exception("批量任务 worker 异常 task_id=%s", task_id)
        try:
            task = db.query(StreamingBatchTask).filter(StreamingBatchTask.id == task_id).first()
            if task and task.status == "running":
                task.status = "failed"
                task.finished_at = datetime.utcnow()
                db.commit()
        except Exception:
            logger.debug("批量任务失败状态写入异常", exc_info=True)
    finally:
        db.close()
        with _run_lock:
            _running_batch_tasks.discard(task_id)


def start_batch_task_async(task_id: int, actor_user_id: int) -> None:
    with _run_lock:
        if task_id in _running_batch_tasks:
            raise RuntimeError("该批量任务已在执行中")
        _running_batch_tasks.add(task_id)
    t = threading.Thread(
        target=run_batch_task_worker,
        args=(task_id, actor_user_id),
        daemon=True,
        name=f"stream-batch-{task_id}",
    )
    t.start()


def create_streaming_batch_task(
    db: Session,
    user,
    *,
    workspace_id: int,
    action: str,
    job_ids: List[int],
    submit_note: Optional[str] = None,
    require_savepoint: bool = True,
    max_jobs: int,
) -> Dict[str, Any]:
    from app.api.streaming import StreamingBatchTask, StreamingBatchTaskItem, StreamingJob

    action = (action or "").strip().lower()
    if action not in VALID_BATCH_ACTIONS:
        raise HTTPException(status_code=400, detail=f"action 须为 start 或 cancel，收到: {action!r}")

    unique_ids = list(dict.fromkeys(int(i) for i in job_ids))
    jobs = (
        db.query(StreamingJob)
        .filter(StreamingJob.workspace_id == workspace_id, StreamingJob.id.in_(unique_ids))
        .all()
    )
    by_id = {j.id: j for j in jobs}
    missing = [i for i in unique_ids if i not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"作业不存在: {missing[:10]}")

    ordered = [by_id[i] for i in unique_ids]
    try:
        validate_batch_job_ids(ordered, action, max_jobs=max_jobs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    task = StreamingBatchTask(
        workspace_id=workspace_id,
        action=action,
        status="queued",
        total=len(ordered),
        submit_note=(submit_note or "").strip() or None,
        require_savepoint=require_savepoint if action == BATCH_ACTION_CANCEL else True,
        created_by=user.id,
    )
    db.add(task)
    db.flush()

    skipped_precount = 0
    for job in ordered:
        item_status = "pending"
        skip_reason = None
        if action == BATCH_ACTION_CANCEL and not _job_has_runtime(job):
            item_status = "skipped"
            skip_reason = "无运行实例，无需停止"
            skipped_precount += 1
        db.add(
            StreamingBatchTaskItem(
                batch_task_id=task.id,
                job_id=job.id,
                job_name=job.name,
                status=item_status,
                error_message=skip_reason,
                finished_at=datetime.utcnow() if skip_reason else None,
            )
        )
    task.skipped = skipped_precount
    db.commit()
    db.refresh(task)

    if action == BATCH_ACTION_START and not workspace_data_full_control(db, user, workspace_id):
        approval = submit_publish_approval(
            db,
            user,
            workspace_id,
            "stream_job_batch",
            int(task.id),
            "batch_submit",
            submit_note=submit_note,
        )
        task.status = "pending_approval"
        task.approval_id = approval.id
        db.commit()
        db.refresh(task)
        return {
            "message": "已提交批量启动审批，通过后系统将依次提交到 Flink",
            "needs_approval": True,
            "approval_id": approval.id,
            "batch_task": serialize_batch_task(db, task),
        }

    start_batch_task_async(task.id, user.id)
    return {
        "message": "批量任务已开始执行",
        "needs_approval": False,
        "batch_task": serialize_batch_task(db, task),
    }


def execute_approved_batch_submit(db: Session, batch_task_id: int, reviewer) -> Dict[str, Any]:
    from app.api.streaming import StreamingBatchTask

    task = db.query(StreamingBatchTask).filter(StreamingBatchTask.id == batch_task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    if task.action != BATCH_ACTION_START:
        raise HTTPException(status_code=400, detail="该批量任务不是启动类型")
    if task.status not in ("pending_approval", "queued"):
        raise HTTPException(status_code=400, detail=f"批量任务状态不可执行: {task.status}")
    task.status = "queued"
    db.commit()
    start_batch_task_async(task.id, reviewer.id)
    return {"message": "批量启动已开始执行", "batch_task_id": task.id}

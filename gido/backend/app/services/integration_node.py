# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据开发 / 工作流 SYNC 节点：绑定数据集成任务并执行。"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.workspace import SyncRecord, SyncTask, TaskNode
from app.services.integration_sync import start_sync_async


def sync_task_id_from_node(node: TaskNode) -> int:
    params = node.params if isinstance(node.params, dict) else {}
    tid = params.get("sync_task_id")
    if tid is not None:
        return int(tid)
    raw = (node.script_content or "").strip()
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            if obj.get("sync_task_id") is not None:
                return int(obj["sync_task_id"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    raise ValueError("SYNC 节点未绑定数据集成任务：请在节点 params 中设置 sync_task_id")


def run_sync_for_node_blocking(
    db: Session,
    node: TaskNode,
    *,
    trigger_type: str = "workflow",
    timeout_seconds: int = 3600,
    poll_interval: float = 1.0,
) -> Tuple[List[str], str, Optional[Dict[str, Any]]]:
    """
    执行绑定的集成任务并阻塞等待结束（供 Studio 运行、工作流、执行器）。
    返回 (log_lines, status, result_meta)
    """
    task_id = sync_task_id_from_node(node)
    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise ValueError(f"数据集成任务 #{task_id} 不存在")
    if task.workspace_id != node.workspace_id:
        raise ValueError("集成任务与节点不属于同一工作空间")
    if not task.is_active:
        raise ValueError(f"数据集成任务「{task.name}」已停用")

    try:
        record = start_sync_async(task_id, trigger_type=trigger_type)
    except RuntimeError as e:
        return [f"[ERROR] {e}"], "failed", None

    record_id = record.id
    deadline = time.time() + max(60, timeout_seconds)
    lines = [
        f"[INFO] 已触发数据集成任务 #{task_id}「{task.name}」",
        f"[INFO] 运行记录 #{record_id}，模式={task.sync_mode}",
    ]

    while time.time() < deadline:
        db.expire_all()
        rec = db.query(SyncRecord).filter(SyncRecord.id == record_id).first()
        if not rec:
            return lines + ["[ERROR] 运行记录丢失"], "failed", None
        if rec.status in ("success", "failed"):
            lines.append(f"[INFO] 状态: {rec.status}")
            lines.append(f"[INFO] 读取 {rec.rows_read or 0} 行，写入 {rec.rows_written or 0} 行")
            if rec.duration_ms:
                lines.append(f"[INFO] 耗时 {(rec.duration_ms or 0) / 1000:.1f}s")
            if rec.error_msg:
                lines.append(f"[ERROR] {rec.error_msg}")
            meta = {
                "sync_task_id": task_id,
                "record_id": record_id,
                "rows_read": rec.rows_read,
                "rows_written": rec.rows_written,
            }
            return lines, rec.status, meta
        time.sleep(poll_interval)

    return lines + [f"[ERROR] 等待超时（{timeout_seconds}s）"], "failed", {"record_id": record_id}

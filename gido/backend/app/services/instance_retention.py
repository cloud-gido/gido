# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-09-11
"""
GIDO 实例热账本留存：只保留最近 N 天，更早的以生产调度（Dolphin）为准。

清理策略刻意「慢、碎、可反复」——运维尽量无感：
- 每轮只删很小一批，并有墙钟时间预算，到点就停
- 条件只用 created_at + 终态，走索引，禁止 OR/函数包列的慢扫
- 按主键 IN 列表删，短事务；批与批之间让出 CPU/IO
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.workspace import AlertEvent, NodeInstance, WorkflowInstance

logger = logging.getLogger(__name__)

# 仍在跑的不删：避免把卡住的运行从账本抹掉，排障会丢线索
_TERMINAL_STATUSES = ("success", "failed", "killed")


def purge_expired_workflow_instances(
    db: Session,
    *,
    retention_days: Optional[int] = None,
    batch_size: Optional[int] = None,
    max_batches: Optional[int] = None,
    time_budget_ms: Optional[int] = None,
    pause_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """
    删除早于留存窗口的终态工作流实例及其节点实例、关联告警。

    以 created_at 判定过期（可走 ix_wi_created_at），不按 finished_at 包函数扫描。
    retention_days<=0 时跳过。
    """
    from app.core.config import settings

    days = int(settings.INSTANCE_RETENTION_DAYS if retention_days is None else retention_days)
    if days <= 0:
        return {"enabled": False, "deleted_instances": 0, "deleted_nodes": 0, "deleted_alerts": 0}

    batch = int(
        settings.INSTANCE_RETENTION_BATCH_SIZE if batch_size is None else batch_size
    )
    # 单批上限压得很低：宁可多跑几轮，也不要一次删出长事务/IO 尖峰
    if batch_size is None:
        batch = max(20, min(batch, 200))
    else:
        batch = max(1, min(batch, 200))
    max_b = int(
        settings.INSTANCE_RETENTION_MAX_BATCHES if max_batches is None else max_batches
    )
    if max_batches is None:
        max_b = max(1, min(max_b, 50))
    else:
        max_b = max(1, min(max_b, 500))
    if time_budget_ms is None:
        budget_ms = max(200, min(int(settings.INSTANCE_RETENTION_TIME_BUDGET_MS), 30_000))
    else:
        budget_ms = max(1, int(time_budget_ms))
    if pause_ms is None:
        gap_ms = max(0, min(int(settings.INSTANCE_RETENTION_PAUSE_MS), 2000))
    else:
        gap_ms = max(0, int(pause_ms))

    cutoff = datetime.utcnow() - timedelta(days=days)
    started = time.monotonic()
    deadline = started + (budget_ms / 1000.0)

    deleted_instances = 0
    deleted_nodes = 0
    deleted_alerts = 0
    batches = 0
    stopped_reason = "drained"

    while batches < max_b:
        if time.monotonic() >= deadline:
            stopped_reason = "time_budget"
            break

        # 索引友好：created_at < cutoff AND status IN (...)，LIMIT 很小
        ids = [
            int(r[0])
            for r in (
                db.query(WorkflowInstance.id)
                .filter(
                    WorkflowInstance.created_at < cutoff,
                    WorkflowInstance.status.in_(_TERMINAL_STATUSES),
                )
                .order_by(WorkflowInstance.created_at.asc(), WorkflowInstance.id.asc())
                .limit(batch)
                .all()
            )
        ]
        if not ids:
            stopped_reason = "drained"
            break
        batches += 1

        # 子实例若指回即将删除的父实例，先断开，避免自引用外键挡住删除
        db.query(WorkflowInstance).filter(WorkflowInstance.parent_instance_id.in_(ids)).update(
            {WorkflowInstance.parent_instance_id: None},
            synchronize_session=False,
        )

        n_alerts = (
            db.query(AlertEvent)
            .filter(AlertEvent.workflow_instance_id.in_(ids))
            .delete(synchronize_session=False)
        )
        n_nodes = (
            db.query(NodeInstance)
            .filter(NodeInstance.workflow_instance_id.in_(ids))
            .delete(synchronize_session=False)
        )
        n_inst = (
            db.query(WorkflowInstance)
            .filter(WorkflowInstance.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()

        deleted_alerts += int(n_alerts or 0)
        deleted_nodes += int(n_nodes or 0)
        deleted_instances += int(n_inst or 0)

        if gap_ms > 0 and batches < max_b and time.monotonic() < deadline:
            time.sleep(gap_ms / 1000.0)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    out = {
        "enabled": True,
        "retention_days": days,
        "cutoff": cutoff.replace(microsecond=0).isoformat(),
        "batches": batches,
        "batch_size": batch,
        "deleted_instances": deleted_instances,
        "deleted_nodes": deleted_nodes,
        "deleted_alerts": deleted_alerts,
        "elapsed_ms": elapsed_ms,
        "stopped_reason": stopped_reason,
    }
    if deleted_instances:
        logger.info("实例热账本留存清理 %s", out)
    return out

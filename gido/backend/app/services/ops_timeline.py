# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""实例中心 / 告警中心共用的「最近发生」时间口径。

列表排序必须与页面时间列一致：按引擎真实运行时间，而不是 GIDO 入库/同步时刻。
从调度回填历史实例时 created_at 是同步时刻，用它排序会把旧跑次插进新跑次中间。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import desc, func
from sqlalchemy.sql import ColumnElement

from app.models.workspace import AlertEvent, NodeInstance, WorkflowInstance


def workflow_instance_list_order_by() -> Sequence[ColumnElement]:
    """实例中心：按实际开始时间倒序（对齐调度侧「最近运行」）。"""
    return (
        desc(WorkflowInstance.started_at),
        desc(WorkflowInstance.id),
    )


def alert_occurred_at_sql(
    *,
    include_node: bool = True,
) -> ColumnElement:
    """
    告警「发生时间」SQL 表达式，须已 OUTER JOIN WorkflowInstance；
    include_node=True 时还须 OUTER JOIN NodeInstance。
    优先级与 resolve_alert_occurred_at 一致。
    """
    if include_node:
        return func.coalesce(
            NodeInstance.finished_at,
            WorkflowInstance.finished_at,
            NodeInstance.started_at,
            WorkflowInstance.started_at,
            AlertEvent.created_at,
        )
    return func.coalesce(
        WorkflowInstance.finished_at,
        WorkflowInstance.started_at,
        AlertEvent.created_at,
    )


def alert_event_list_order_by(*, include_node: bool = True) -> Sequence[ColumnElement]:
    """告警中心：按发生时间倒序（与「发生时间」列一致）。"""
    return (
        desc(alert_occurred_at_sql(include_node=include_node)),
        desc(AlertEvent.id),
    )


def resolve_alert_occurred_at(
    alert: Any,
    wf_inst: Any = None,
    node_inst: Any = None,
) -> Optional[datetime]:
    """序列化用：告警发生时间（节点/工作流结束或开始，否则入库时间）。"""
    return (
        getattr(node_inst, "finished_at", None)
        or getattr(wf_inst, "finished_at", None)
        or getattr(node_inst, "started_at", None)
        or getattr(wf_inst, "started_at", None)
        or getattr(alert, "created_at", None)
    )

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.workspace import AlertEvent, NodeInstance, Workflow, WorkflowInstance


def open_instance_alert(
    db: Session,
    *,
    workflow_instance: WorkflowInstance,
    node_instance: Optional[NodeInstance] = None,
    message: str = "",
    alert_type: str = "failed",
    level: str = "error",
) -> Optional[AlertEvent]:
    """按实例/节点幂等打开告警。"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_instance.workflow_id).first()
    dedupe_key = (
        f"{alert_type}:workflow:{workflow_instance.id}:node:{node_instance.id}"
        if node_instance
        else f"{alert_type}:workflow:{workflow_instance.id}"
    )
    exists = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.dedupe_key == dedupe_key,
            AlertEvent.status == "open",
        )
        .first()
    )
    if exists:
        return exists
    event = AlertEvent(
        workspace_id=wf.workspace_id if wf else None,
        workflow_id=workflow_instance.workflow_id,
        workflow_instance_id=workflow_instance.id,
        node_instance_id=node_instance.id if node_instance else None,
        alert_type=alert_type,
        level=level,
        severity=level,
        dedupe_key=dedupe_key,
        notification_status="pending",
        status="open",
        message=message or f"实例 #{workflow_instance.id} 执行失败",
    )
    db.add(event)
    db.flush()
    try:
        from app.services.alert_notification import notify_alert_event

        notify_alert_event(db, event)
    except Exception:
        # 告警入库优先，通知失败只影响 notification_status，不阻断状态同步。
        event.notification_status = "failed"
    return event

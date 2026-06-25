# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import AlertEvent, AlertNotificationConfig, NodeInstance, TaskNode, Workflow, WorkflowInstance
from app.services.rbac import assert_workspace_access, check_workspace_permission
from app.services.alert_notification import (
    notify_alert_event,
    serialize_alert_notification_config,
    upsert_alert_notification_config,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(
    workspace_id: int,
    status: Optional[str] = Query("open"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    assert_workspace_access(db, current_user, workspace_id)
    q = db.query(AlertEvent).filter(AlertEvent.workspace_id == workspace_id)
    if status:
        q = q.filter(AlertEvent.status == status)
    total = q.count()
    rows = q.order_by(AlertEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    workflow_names = {
        wf.id: wf.name
        for wf in db.query(Workflow).filter(Workflow.id.in_([r.workflow_id for r in rows if r.workflow_id])).all()
    }
    workflow_instances = {
        inst.id: inst
        for inst in db.query(WorkflowInstance)
        .filter(WorkflowInstance.id.in_([r.workflow_instance_id for r in rows if r.workflow_instance_id]))
        .all()
    }
    node_instances = {
        ni.id: ni
        for ni in db.query(NodeInstance)
        .filter(NodeInstance.id.in_([r.node_instance_id for r in rows if r.node_instance_id]))
        .all()
    }
    task_nodes = {
        node.id: node
        for node in db.query(TaskNode)
        .filter(TaskNode.id.in_([ni.node_id for ni in node_instances.values() if ni.node_id]))
        .all()
    }
    items = []
    for r in rows:
        wf_inst = workflow_instances.get(r.workflow_instance_id)
        node_inst = node_instances.get(r.node_instance_id)
        node = task_nodes.get(node_inst.node_id) if node_inst else None
        occurred_at = (
            getattr(node_inst, "finished_at", None)
            or getattr(wf_inst, "finished_at", None)
            or getattr(node_inst, "started_at", None)
            or getattr(wf_inst, "started_at", None)
            or r.created_at
        )
        log_summary = ""
        if node_inst and node_inst.log_content:
            log_summary = str(node_inst.log_content).strip().splitlines()[0][:300]
        items.append({
            "id": r.id,
            "workspace_id": r.workspace_id,
            "workflow_id": r.workflow_id,
            "workflow_name": workflow_names.get(r.workflow_id),
            "workflow_instance_id": r.workflow_instance_id,
            "workflow_instance_status": getattr(wf_inst, "status", None),
            "business_date": getattr(wf_inst, "business_date", None),
            "trigger_type": getattr(wf_inst, "trigger_type", None),
            "scheduler_instance_id": getattr(wf_inst, "scheduler_instance_id", None),
            "node_instance_id": r.node_instance_id,
            "node_instance_status": getattr(node_inst, "status", None),
            "node_name": getattr(node, "name", None),
            "node_type": getattr(node, "node_type", None),
            "scheduler_task_instance_id": getattr(node_inst, "scheduler_task_instance_id", None),
            "scheduler_task_code": getattr(node_inst, "scheduler_task_code", None),
            "log_summary": log_summary,
            "alert_type": r.alert_type,
            "level": r.level,
            "severity": getattr(r, "severity", None) or r.level,
            "dedupe_key": getattr(r, "dedupe_key", None),
            "assignee_id": getattr(r, "assignee_id", None),
            "assignee_group": getattr(r, "assignee_group", None),
            "notification_status": getattr(r, "notification_status", None),
            "status": r.status,
            "message": r.message,
            "occurred_at": occurred_at,
            "created_at": r.created_at,
            "ack_by": r.ack_by,
            "ack_at": r.ack_at,
            "resolved_at": r.resolved_at,
        })
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


@router.post("/{alert_id}/ack")
def ack_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    row.status = "acknowledged"
    row.ack_by = current_user.id
    row.ack_at = datetime.utcnow()
    db.commit()
    return {"message": "已确认告警", "id": row.id}


@router.post("/{alert_id}/resolve")
def resolve_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    row.status = "resolved"
    row.resolved_at = datetime.utcnow()
    db.commit()
    return {"message": "已解决告警", "id": row.id}


@router.post("/{alert_id}/assign")
def assign_alert(
    alert_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    data = payload or {}
    row.assignee_id = data.get("assignee_id")
    row.assignee_group = data.get("assignee_group")
    db.commit()
    return {"message": "已指派告警", "id": row.id}


@router.post("/{alert_id}/notify")
def notify_alert(
    alert_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    data = payload or {}
    result = notify_alert_event(db, row, force=bool(data.get("force", True)))
    db.commit()
    return {"message": "已发送告警通知", "id": row.id, "notification_status": row.notification_status, **result}


@router.get("/notification/config")
def get_notification_config(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    assert_workspace_access(db, current_user, workspace_id)
    cfg = db.query(AlertNotificationConfig).filter(AlertNotificationConfig.workspace_id == workspace_id).first()
    return serialize_alert_notification_config(cfg)


@router.put("/notification/config")
def put_notification_config(
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    cfg = upsert_alert_notification_config(db, workspace_id, payload or {}, getattr(current_user, "id", None))
    db.commit()
    db.refresh(cfg)
    return serialize_alert_notification_config(cfg)


@router.post("/notification/test")
def test_notification_config(
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    data = payload or {}
    cfg = upsert_alert_notification_config(db, workspace_id, data, getattr(current_user, "id", None))
    event = AlertEvent(
        workspace_id=workspace_id,
        alert_type="test",
        level="info",
        severity="info",
        status="open",
        message="这是一条 GIDO 告警通知测试消息。",
        notification_status="pending",
    )
    db.add(event)
    db.flush()
    result = notify_alert_event(db, event, force=True)
    db.rollback()
    return {"message": "测试完成", "notification_status": event.notification_status, **result}

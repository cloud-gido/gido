# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.models.workspace import (
    AlertEvent,
    AlertNotificationConfig,
    NodeInstance,
    TaskNode,
    Workflow,
    WorkflowInstance,
)


def failed_node_names(db: Session, workflow_instance: WorkflowInstance) -> list[str]:
    rows = (
        db.query(NodeInstance)
        .filter(
            NodeInstance.workflow_instance_id == workflow_instance.id,
            NodeInstance.status == "failed",
        )
        .all()
    )
    names: list[str] = []
    for ni in rows:
        node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first() if ni.node_id else None
        names.append((node.name if node else "") or f"节点#{ni.node_id}")
    return names


def open_instance_alert(
    db: Session,
    *,
    workflow_instance: WorkflowInstance,
    node_instance: Optional[NodeInstance] = None,
    message: str = "",
    alert_type: str = "failed",
    level: str = "error",
    notify: bool = True,
    force_notify: bool = False,
) -> Optional[AlertEvent]:
    """按实例/节点幂等打开告警。默认立刻按工作区配置推送；节点级可只入库不推送。"""
    wf = db.query(Workflow).filter(Workflow.id == workflow_instance.workflow_id).first()
    if wf:
        wf_name = (wf.name or "").strip() or f"#{workflow_instance.id}"
    else:
        wf_name = f"#{workflow_instance.id}"
    if not message:
        if node_instance:
            message = f"工作流 {wf_name} 节点失败（实例 #{workflow_instance.id}）"
        else:
            extra = ""
            names = failed_node_names(db, workflow_instance)
            if names:
                extra = f"；失败节点：{', '.join(names[:12])}"
            biz = getattr(workflow_instance, "business_date", None) or ""
            biz_part = f"；业务日期 {biz}" if biz else ""
            message = f"工作流 {wf_name} 执行失败（实例 #{workflow_instance.id}）{biz_part}{extra}"
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
        if notify and exists.notification_status in (None, "pending", "skipped", "failed"):
            try:
                from app.services.alert_notification import notify_alert_event

                notify_alert_event(db, exists, force=force_notify)
            except Exception:
                exists.notification_status = "failed"
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
        notification_status="pending" if notify else "skipped",
        status="open",
        message=message or f"实例 #{workflow_instance.id} 执行失败",
    )
    db.add(event)
    db.flush()
    if not notify:
        event.notification_status = "skipped"
        return event
    try:
        from app.services.alert_notification import notify_alert_event

        notify_alert_event(db, event, force=force_notify)
    except Exception:
        event.notification_status = "failed"
    return event


def resolve_instance_alerts_on_recovery(db: Session, workflow_instance: WorkflowInstance) -> Optional[AlertEvent]:
    """实例从失败恢复成功：关闭未处理失败告警，并推一条恢复通知。"""
    opens = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.workflow_instance_id == workflow_instance.id,
            AlertEvent.status.in_(("open", "acknowledged")),
            AlertEvent.alert_type == "failed",
        )
        .all()
    )
    if not opens:
        return None
    now = datetime.utcnow()
    for ev in opens:
        ev.status = "resolved"
        ev.resolved_at = now
    wf = db.query(Workflow).filter(Workflow.id == workflow_instance.workflow_id).first()
    wf_name = (wf.name if wf and wf.name else "") or f"#{workflow_instance.id}"
    return open_instance_alert(
        db,
        workflow_instance=workflow_instance,
        alert_type="recovered",
        level="info",
        message=f"工作流 {wf_name} 已恢复成功（实例 #{workflow_instance.id}）",
        notify=True,
        force_notify=True,
    )


def workspace_alert_coverage(db: Session, workspace_id: int) -> dict:
    """值班配置是否能覆盖本空间已发布工作流（配错空间、未配站点入口等）。"""
    from app.services.alert_notification import gido_public_url

    cfg = db.query(AlertNotificationConfig).filter(AlertNotificationConfig.workspace_id == workspace_id).first()
    published = (
        db.query(Workflow)
        .filter(
            Workflow.workspace_id == workspace_id,
            or_(
                Workflow.scheduler_definition_id.isnot(None),
                Workflow.status == "published",
            ),
        )
        .order_by(Workflow.name)
        .all()
    )
    names = [(w.name or "").strip() or f"#{w.id}" for w in published]
    lark_on = bool(cfg and cfg.lark_enabled)
    site = bool((gido_public_url(db) or "").strip())
    hints: list[str] = []
    if not published:
        hints.append("本空间没有已发布到调度的工作流。Dolphin 上的失败不会进本空间告警中心，请确认工作流是从哪个空间发布的，并在该空间配置飞书。")
    elif not lark_on:
        sample = "、".join(names[:4])
        suffix = f" 等共 {len(names)} 条" if len(names) > 4 else ""
        hints.append(f"本空间已发布 {len(names)} 条调度工作流（{sample}{suffix}），但飞书渠道未打开。失败会进告警中心，不会推值班群。")
    if not site:
        hints.append("尚未配置站点入口（系统管理 → 平台集成）。飞书卡片无法跳转实例中心或告警中心。")
    return {
        "published_workflow_count": len(published),
        "published_workflow_names": names[:8],
        "lark_enabled": lark_on,
        "site_url_configured": site,
        "hints": hints,
    }

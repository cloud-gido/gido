# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


def _on_call_assignee(db: Session, workspace_id: Optional[int]) -> Optional[int]:
    """新告警直接落到当班人名下；没排班就留空，由人自己认领。"""
    if not workspace_id:
        return None
    try:
        from app.services.alert_oncall import resolve_on_call

        users = resolve_on_call(db, int(workspace_id))
    except Exception:
        logger.debug("解析值班人失败 workspace_id=%s", workspace_id, exc_info=True)
        return None
    return int(users[0].id) if users else None


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
        assignee_id=_on_call_assignee(db, wf.workspace_id if wf else None),
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


def open_workflow_alert(
    db: Session,
    *,
    workflow: Workflow,
    dedupe_key: str,
    message: str,
    alert_type: str,
    level: str = "error",
    notify: bool = True,
) -> Optional[AlertEvent]:
    """
    打开一条不挂在实例上的工作流级告警。

    基线破线的典型场景是「到点了根本没有这次运行」，此时没有实例可以挂，
    所以去重键由调用方给出（一般带上业务日期）。
    """
    exists = (
        db.query(AlertEvent)
        .filter(AlertEvent.dedupe_key == dedupe_key, AlertEvent.status == "open")
        .first()
    )
    if exists:
        return exists
    event = AlertEvent(
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        alert_type=alert_type,
        level=level,
        severity=level,
        dedupe_key=dedupe_key,
        notification_status="pending" if notify else "skipped",
        status="open",
        message=message,
        assignee_id=_on_call_assignee(db, workflow.workspace_id),
    )
    db.add(event)
    db.flush()
    if not notify:
        event.notification_status = "skipped"
        return event
    try:
        from app.services.alert_notification import notify_alert_event

        notify_alert_event(db, event)
    except Exception:
        event.notification_status = "failed"
    return event


def resolve_alerts(
    db: Session, *, dedupe_key: Optional[str] = None, workflow_instance_id: Optional[int] = None,
    alert_types: tuple[str, ...] = ("failed",),
) -> int:
    """关闭匹配的未处理告警，返回关闭条数。恢复通知由调用方决定是否推送。"""
    q = db.query(AlertEvent).filter(
        AlertEvent.status.in_(("open", "acknowledged")),
        AlertEvent.alert_type.in_(alert_types),
    )
    if dedupe_key is not None:
        q = q.filter(AlertEvent.dedupe_key == dedupe_key)
    if workflow_instance_id is not None:
        q = q.filter(AlertEvent.workflow_instance_id == workflow_instance_id)
    rows = q.all()
    now = datetime.utcnow()
    for ev in rows:
        ev.status = "resolved"
        ev.resolved_at = now
    return len(rows)


def resolve_instance_alerts_on_recovery(db: Session, workflow_instance: WorkflowInstance) -> Optional[AlertEvent]:
    """
    实例恢复成功：关闭该实例的失败与超时告警，并为失败推一条恢复通知。
    超时告警自己不推恢复卡片——跑得慢但最终成功，没必要再刷一次群。
    """
    had_failure = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.workflow_instance_id == workflow_instance.id,
            AlertEvent.status.in_(("open", "acknowledged")),
            AlertEvent.alert_type == "failed",
        )
        .first()
        is not None
    )
    resolve_alerts(
        db, workflow_instance_id=workflow_instance.id, alert_types=("failed", "timeout")
    )
    if not had_failure:
        return None
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
    unbound = [
        (w.name or "").strip() or f"#{w.id}"
        for w in published
        if not (getattr(w, "scheduler_definition_id", None) or "").strip()
        or not (getattr(w, "scheduler_project_id", None) or "").strip()
    ]
    lark_on = bool(cfg and cfg.lark_enabled)
    site = bool((gido_public_url(db) or "").strip())
    hints: list[str] = []
    if not published:
        hints.append("本空间没有已发布到生产调度的工作流。生产失败不会进本空间告警中心，请确认工作流是从哪个空间发布的，并在该空间配置飞书。")
    elif unbound:
        sample = "、".join(unbound[:4])
        suffix = f" 等共 {len(unbound)} 条" if len(unbound) > 4 else ""
        hints.append(
            f"有 {len(unbound)} 条工作流标记为已发布，但没有生效的生产调度绑定（{sample}{suffix}）。"
            "它们的运行与失败不会进入实例中心和告警中心，请重新发布。"
        )
    if published and not lark_on:
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

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from typing import Iterable, Optional

import httpx
from sqlalchemy.orm import Session

from app.core.brand import BRAND_SUITE
from app.core.config import settings
from app.models.workspace import AlertEvent, AlertNotificationConfig, NodeInstance, TaskNode, User, Workflow, WorkflowInstance

logger = logging.getLogger(__name__)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def _masked(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    s = str(value)
    if len(s) <= 10:
        return "***"
    return f"{s[:6]}***{s[-4:]}"


def serialize_alert_notification_config(cfg: Optional[AlertNotificationConfig]) -> dict:
    if not cfg:
        return {
            "enabled": False,
            "min_severity": "error",
            "email_enabled": False,
            "email_to": "",
            "smtp_host": settings.SMTP_HOST or "",
            "smtp_port": settings.SMTP_PORT,
            "smtp_user": "",
            "smtp_password_masked": None,
            "smtp_from": settings.SMTP_FROM,
            "smtp_tls": False,
            "webhook_enabled": bool(settings.ALERT_WEBHOOK_URL),
            "webhook_url_masked": _masked(settings.ALERT_WEBHOOK_URL),
            "lark_enabled": False,
            "lark_webhook_url_masked": None,
            "wecom_enabled": False,
            "wecom_webhook_url_masked": None,
        }
    return {
        "enabled": bool(cfg.enabled),
        "min_severity": cfg.min_severity or "error",
        "email_enabled": bool(cfg.email_enabled),
        "email_to": cfg.email_to or "",
        "smtp_host": cfg.smtp_host or "",
        "smtp_port": cfg.smtp_port or 25,
        "smtp_user": cfg.smtp_user or "",
        "smtp_password_masked": _masked(cfg.smtp_password),
        "smtp_from": cfg.smtp_from or settings.SMTP_FROM,
        "smtp_tls": bool(cfg.smtp_tls),
        "webhook_enabled": bool(cfg.webhook_enabled),
        "webhook_url_masked": _masked(cfg.webhook_url),
        "lark_enabled": bool(cfg.lark_enabled),
        "lark_webhook_url_masked": _masked(cfg.lark_webhook_url),
        "wecom_enabled": bool(cfg.wecom_enabled),
        "wecom_webhook_url_masked": _masked(cfg.wecom_webhook_url),
        "updated_at": cfg.updated_at,
        "updated_by": cfg.updated_by,
    }


def upsert_alert_notification_config(db: Session, workspace_id: int, payload: dict, user_id: Optional[int]) -> AlertNotificationConfig:
    cfg = db.query(AlertNotificationConfig).filter(AlertNotificationConfig.workspace_id == workspace_id).first()
    if not cfg:
        cfg = AlertNotificationConfig(workspace_id=workspace_id)
        db.add(cfg)
    direct_fields = (
        "enabled",
        "min_severity",
        "email_enabled",
        "email_to",
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_from",
        "smtp_tls",
        "webhook_enabled",
        "lark_enabled",
        "wecom_enabled",
    )
    for key in direct_fields:
        if key in payload:
            setattr(cfg, key, payload.get(key))
    # Secret fields are write-only unless explicitly provided.
    for key in ("smtp_password", "webhook_url", "lark_webhook_url", "wecom_webhook_url"):
        val = payload.get(key)
        if val is not None and str(val).strip() != "":
            setattr(cfg, key, str(val).strip())
    cfg.updated_by = user_id
    db.flush()
    return cfg


def _split_recipients(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in str(raw).replace(";", ",").split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def _alert_context(db: Session, event: AlertEvent) -> dict:
    wf = db.query(Workflow).filter(Workflow.id == event.workflow_id).first() if event.workflow_id else None
    inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == event.workflow_instance_id).first() if event.workflow_instance_id else None
    ni = db.query(NodeInstance).filter(NodeInstance.id == event.node_instance_id).first() if event.node_instance_id else None
    node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first() if ni and ni.node_id else None
    return {"workflow": wf, "instance": inst, "node_instance": ni, "node": node}


def render_alert_message(db: Session, event: AlertEvent) -> tuple[str, str]:
    ctx = _alert_context(db, event)
    wf = ctx["workflow"]
    inst = ctx["instance"]
    ni = ctx["node_instance"]
    node = ctx["node"]
    title = f"{BRAND_SUITE} 告警：{wf.name if wf else '工作流'} {event.alert_type}"
    lines = [
        f"告警级别：{getattr(event, 'severity', None) or event.level or 'error'}",
        f"告警状态：{event.status}",
        f"工作流：{wf.name if wf else '-'}",
        f"工作流实例：#{event.workflow_instance_id or '-'}",
    ]
    if inst:
        lines.extend([
            f"实例状态：{inst.status}",
            f"业务日期：{inst.business_date or '-'}",
            f"触发方式：{inst.trigger_type or '-'}",
        ])
    if ni:
        lines.extend([
            f"节点：{node.name if node else f'#{ni.node_id}'}",
            f"节点类型：{node.node_type if node else '-'}",
            f"节点实例：#{ni.id}",
            f"节点状态：{ni.status}",
        ])
    lines.append(f"告警内容：{event.message or '-'}")
    return title, "\n".join(lines)


def _send_email(cfg: AlertNotificationConfig, title: str, content: str) -> None:
    host = cfg.smtp_host or settings.SMTP_HOST
    recipients = _split_recipients(cfg.email_to or settings.ALERT_EMAIL)
    if not host or not recipients:
        return
    sender = cfg.smtp_from or settings.SMTP_FROM
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = f"【{BRAND_SUITE}告警】{title}"
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    with smtplib.SMTP(host, int(cfg.smtp_port or settings.SMTP_PORT or 25), timeout=10) as smtp:
        if cfg.smtp_tls:
            smtp.starttls()
        if cfg.smtp_user and cfg.smtp_password:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.sendmail(sender, recipients, msg.as_string())


def _post_json(url: str, payload: dict) -> None:
    resp = httpx.post(url, json=payload, timeout=8)
    resp.raise_for_status()


def _enabled_channels(cfg: AlertNotificationConfig) -> Iterable[str]:
    if cfg.email_enabled:
        yield "email"
    if cfg.webhook_enabled and cfg.webhook_url:
        yield "webhook"
    if cfg.lark_enabled and cfg.lark_webhook_url:
        yield "lark"
    if cfg.wecom_enabled and cfg.wecom_webhook_url:
        yield "wecom"


def notify_alert_event(db: Session, event: AlertEvent, *, force: bool = False) -> dict:
    cfg = db.query(AlertNotificationConfig).filter(AlertNotificationConfig.workspace_id == event.workspace_id).first()
    if not cfg:
        # Environment fallback for simple deployments.
        cfg = AlertNotificationConfig(
            workspace_id=event.workspace_id or 0,
            enabled=bool(settings.ALERT_WEBHOOK_URL or settings.ALERT_EMAIL),
            min_severity="error",
            webhook_enabled=bool(settings.ALERT_WEBHOOK_URL),
            webhook_url=settings.ALERT_WEBHOOK_URL,
            email_enabled=bool(settings.ALERT_EMAIL),
            email_to=settings.ALERT_EMAIL,
            smtp_host=settings.SMTP_HOST,
            smtp_port=settings.SMTP_PORT,
            smtp_from=settings.SMTP_FROM,
        )
    severity = getattr(event, "severity", None) or event.level or "error"
    if not force:
        if not cfg.enabled:
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "disabled"}
        if _SEVERITY_ORDER.get(severity, 2) < _SEVERITY_ORDER.get(cfg.min_severity or "error", 2):
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "severity_below_threshold"}

    title, content = render_alert_message(db, event)
    sent: list[str] = []
    failed: list[dict] = []
    for ch in _enabled_channels(cfg):
        try:
            if ch == "email":
                _send_email(cfg, title, content)
            elif ch == "webhook":
                _post_json(cfg.webhook_url, {"title": title, "content": content, "severity": severity, "alert_id": event.id})
            elif ch == "lark":
                _post_json(cfg.lark_webhook_url, {"msg_type": "text", "content": {"text": f"{title}\n{content}"}})
            elif ch == "wecom":
                _post_json(cfg.wecom_webhook_url, {"msgtype": "text", "text": {"content": f"{title}\n{content}"}})
            sent.append(ch)
        except Exception as e:
            logger.warning("alert notification failed alert_id=%s channel=%s: %s", event.id, ch, e)
            failed.append({"channel": ch, "error": str(e)[:300]})
    if failed and sent:
        event.notification_status = "partial"
    elif failed:
        event.notification_status = "failed"
    elif sent:
        event.notification_status = "sent"
    else:
        event.notification_status = "skipped"
    return {"sent": sent, "failed": failed}

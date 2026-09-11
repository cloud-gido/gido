# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Iterable, Optional

import httpx
from sqlalchemy.orm import Session

from app.core.brand import BRAND_SUITE
from app.core.config import settings
from app.models.workspace import AlertEvent, AlertNotificationConfig, NodeInstance, TaskNode, User, Workflow, WorkflowInstance, Workspace
from app.services.alert_oncall import on_call_label, quiet_hours_decision

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
            "notify_cooldown_minutes": 15,
            "muted_until": None,
            "notify_armed_at": None,
            "quiet_hours_enabled": False,
            "quiet_hours_start": "",
            "quiet_hours_end": "",
            "quiet_hours_min_severity": "critical",
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
        "notify_cooldown_minutes": int(getattr(cfg, "notify_cooldown_minutes", None) or 15),
        "muted_until": getattr(cfg, "muted_until", None),
        "notify_armed_at": getattr(cfg, "notify_armed_at", None),
        "quiet_hours_enabled": bool(getattr(cfg, "quiet_hours_enabled", False)),
        "quiet_hours_start": getattr(cfg, "quiet_hours_start", None) or "",
        "quiet_hours_end": getattr(cfg, "quiet_hours_end", None) or "",
        "quiet_hours_min_severity": getattr(cfg, "quiet_hours_min_severity", None) or "critical",
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
    if any((cfg.email_enabled, cfg.webhook_enabled, cfg.lark_enabled, cfg.wecom_enabled)) and payload.get("enabled") is not False:
        cfg.enabled = True
    if "notify_cooldown_minutes" in payload:
        try:
            cfg.notify_cooldown_minutes = max(0, int(payload.get("notify_cooldown_minutes") or 0))
        except (TypeError, ValueError):
            pass
    if "mute_hours" in payload:
        try:
            hours = float(payload.get("mute_hours") or 0)
        except (TypeError, ValueError):
            hours = 0
        if hours > 0:
            cfg.muted_until = datetime.utcnow() + timedelta(hours=hours)
        else:
            cfg.muted_until = None
    _apply_quiet_hours(cfg, payload)
    channels_on = any((cfg.email_enabled, cfg.webhook_enabled, cfg.lark_enabled, cfg.wecom_enabled))
    if payload.get("arm_from_now") or (channels_on and getattr(cfg, "notify_armed_at", None) is None):
        cfg.notify_armed_at = datetime.utcnow()
    db.flush()
    return cfg


def _apply_quiet_hours(cfg: AlertNotificationConfig, payload: dict) -> None:
    """静默时段配置：起止必须成对且合法，否则直接拒绝——半配一半会静默得莫名其妙。"""
    from app.services.alert_oncall import DEFAULT_QUIET_MIN_SEVERITY, parse_hhmm

    if "quiet_hours_start" in payload or "quiet_hours_end" in payload:
        start = str(payload.get("quiet_hours_start") or "").strip()
        end = str(payload.get("quiet_hours_end") or "").strip()
        if start or end:
            if parse_hhmm(start) is None or parse_hhmm(end) is None:
                raise ValueError("静默时段起止须为 HH:MM，且需同时填写")
            if start == end:
                raise ValueError("静默时段起止不能相同")
        cfg.quiet_hours_start = start or None
        cfg.quiet_hours_end = end or None
    if "quiet_hours_min_severity" in payload:
        sev = str(payload.get("quiet_hours_min_severity") or "").strip() or DEFAULT_QUIET_MIN_SEVERITY
        if sev not in _SEVERITY_ORDER:
            raise ValueError(f"静默时段放行级别须为 {'/'.join(_SEVERITY_ORDER)} 之一")
        cfg.quiet_hours_min_severity = sev
    if "quiet_hours_enabled" in payload:
        want = bool(payload.get("quiet_hours_enabled"))
        if want and not (cfg.quiet_hours_start and cfg.quiet_hours_end):
            raise ValueError("启用静默时段前请先填写起止时间")
        cfg.quiet_hours_enabled = want


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


_ALERT_TYPE_LABELS = {
    "failed": "执行失败",
    "recovered": "已恢复",
    "sla": "未按时完成",
    "timeout": "运行超时",
    "test": "通道测试",
}


def alert_type_label(alert_type: Optional[str]) -> str:
    key = (alert_type or "failed") or "failed"
    return _ALERT_TYPE_LABELS.get(key, key)


def render_alert_message(db: Session, event: AlertEvent) -> tuple[str, str]:
    ctx = _alert_context(db, event)
    wf = ctx["workflow"]
    inst = ctx["instance"]
    ni = ctx["node_instance"]
    node = ctx["node"]
    title = f"{BRAND_SUITE} 告警：{wf.name if wf else '工作流'} {alert_type_label(event.alert_type)}"
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


def _smtp_connect(host: str, port: int, use_tls: bool):
    """465 使用 SMTP_SSL；587/25 使用 SMTP，可选 STARTTLS（163/QQ 等常见配置）。"""
    timeout = 30
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=timeout)
    smtp = smtplib.SMTP(host, port, timeout=timeout)
    if use_tls:
        smtp.starttls()
    return smtp


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
    port = int(cfg.smtp_port or settings.SMTP_PORT or 25)
    smtp = _smtp_connect(host, port, bool(cfg.smtp_tls))
    try:
        if cfg.smtp_user and cfg.smtp_password:
            smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.sendmail(sender, recipients, msg.as_string())
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


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


def normalize_gido_public_url(raw: Optional[str], *, require_http: bool = False) -> Optional[str]:
    s = (raw or "").strip().rstrip("/")
    if not s:
        return None
    if require_http and not (s.startswith("http://") or s.startswith("https://")):
        raise ValueError("须以 http:// 或 https:// 开头，不要末尾斜杠")
    return s


def gido_public_url(db: Optional[Session] = None) -> str:
    """平台集成库覆盖优先，否则环境变量 GIDO_PUBLIC_URL。"""
    if db is not None:
        try:
            from app.services.ds_runtime import ensure_platform_integration_row

            row = ensure_platform_integration_row(db)
            from_db = normalize_gido_public_url(getattr(row, "gido_public_url", None))
            if from_db:
                return from_db
        except Exception:
            logger.debug("gido_public_url db lookup failed", exc_info=True)
    return normalize_gido_public_url(getattr(settings, "GIDO_PUBLIC_URL", None)) or ""


def instance_ops_url(
    workspace_id: Optional[int],
    instance_id: Optional[int],
    db: Optional[Session] = None,
) -> Optional[str]:
    base = gido_public_url(db)
    if not base or not workspace_id or not instance_id:
        return None
    return f"{base}/gido/batch/operation?workspace_id={int(workspace_id)}&instance={int(instance_id)}"


def alerts_center_url(workspace_id: Optional[int], db: Optional[Session] = None) -> Optional[str]:
    base = gido_public_url(db)
    if not base or not workspace_id:
        return None
    return f"{base}/gido/batch/alert?workspace_id={int(workspace_id)}"


def _fmt_local(dt: Optional[datetime], tz_name: str) -> str:
    if not dt:
        return "—"
    try:
        from zoneinfo import ZoneInfo

        aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        return aware.astimezone(ZoneInfo(tz_name or "Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt)


def _event_occurred_at(db: Session, event: AlertEvent) -> Optional[datetime]:
    ctx = _alert_context(db, event)
    inst = ctx["instance"]
    ni = ctx["node_instance"]
    for dt in (
        getattr(ni, "finished_at", None) if ni else None,
        getattr(inst, "finished_at", None) if inst else None,
        getattr(inst, "started_at", None) if inst else None,
        event.created_at,
    ):
        if dt:
            return dt
    return None


def _before_notify_arm(db: Session, event: AlertEvent, cfg: AlertNotificationConfig) -> bool:
    """实例结束于推送起点之前：只入库不推送（打开配置后不再刷历史）。"""
    armed = getattr(cfg, "notify_armed_at", None)
    if armed is None:
        armed = datetime.utcnow() - timedelta(minutes=5)
    occurred = _event_occurred_at(db, event)
    if occurred is None:
        return False
    try:
        return occurred.replace(tzinfo=None) < armed.replace(tzinfo=None)
    except Exception:
        return False


def _lark_field(label: str, value: str) -> dict:
    text = (value or "—").strip() or "—"
    return {
        "is_short": True,
        "text": {"tag": "lark_md", "content": f"**{label}**\n{text}"},
    }


def _lark_card_payload(db: Session, event: AlertEvent, title: str, content: str) -> dict:
    kind = (event.alert_type or "failed") or "failed"
    ctx = _alert_context(db, event)
    wf = ctx["workflow"]
    inst = ctx["instance"]
    ws = db.query(Workspace).filter(Workspace.id == event.workspace_id).first() if event.workspace_id else None
    tz = (getattr(ws, "timezone", None) or "Asia/Shanghai")
    wf_name = (wf.name if wf and wf.name else "") or "—"
    if kind == "test" or event.workflow_instance_id is None:
        # 基线破线时往往连实例都没有，只能落到告警中心
        url = alerts_center_url(event.workspace_id, db)
        button_label = "打开告警中心"
    else:
        url = instance_ops_url(event.workspace_id, event.workflow_instance_id, db)
        button_label = "查看详情"

    if kind == "test":
        header_title = f"{BRAND_SUITE} · 通道测试"
        template = "wathet"
    elif kind == "recovered":
        header_title = f"{BRAND_SUITE} · 已恢复"
        template = "green"
    elif kind == "sla":
        header_title = f"{BRAND_SUITE} · 未按时完成"
        template = "orange"
    elif kind == "timeout":
        header_title = f"{BRAND_SUITE} · 运行超时"
        template = "orange"
    else:
        header_title = f"{BRAND_SUITE} · 调度失败"
        template = "red"
    if wf_name and kind != "test":
        header_title = f"{header_title} · {wf_name}"[:40]

    elements: list[dict] = []
    if kind == "test":
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "这是一条**连通性测试**，不是真实调度失败。"},
        })
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "fields": [
                _lark_field("工作空间", (ws.name if ws else "") or "—"),
                _lark_field("时间", _fmt_local(datetime.utcnow(), tz)),
            ],
        })
    else:
        trigger = ""
        if inst:
            try:
                from app.services.workflow_trigger_display import format_trigger_type_label

                trigger = format_trigger_type_label(
                    inst.trigger_type,
                    getattr(inst, "dolphin_command_type", None),
                    getattr(inst, "scheduler_instance_id", None),
                ) or (inst.trigger_type or "")
            except Exception:
                trigger = inst.trigger_type or ""
        elements.append({
            "tag": "div",
            "fields": [
                _lark_field("工作流", wf_name),
                _lark_field("业务日期", (inst.business_date if inst else None) or "—"),
                _lark_field("实例", f"#{inst.id}" if inst else "—"),
                _lark_field("结束时间", _fmt_local(getattr(inst, "finished_at", None), tz)),
            ],
        })
        elements.append({"tag": "hr"})
        if kind == "recovered":
            focus = "🟢 工作流已恢复成功"
        elif kind == "sla":
            focus = f"**需关注**\n🟠 {event.message or '未按时完成'}"
        elif kind == "timeout":
            focus = f"**需关注**\n🟠 {event.message or '运行超时'}"
        else:
            from app.services.alert_center import failed_node_names

            names = failed_node_names(db, inst) if inst else []
            node_lines = "\n".join(f"🔴 {n}" for n in names[:8]) or "尚未同步到失败节点名"
            if len(names) > 8:
                node_lines += f"\n…共 {len(names)} 个失败节点"
            focus = f"**需关注**\n{node_lines}"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": focus}})
        elements.append({"tag": "hr"})
        if kind == "recovered":
            action = "**建议动作**\n• 确认下游任务是否需要补数\n• 在实例中心核对本次运行日志"
        elif kind == "sla":
            action = "**建议动作**\n• 确认上游依赖是否卡住\n• 评估是否需要人工补数并通知下游"
        elif kind == "timeout":
            action = "**建议动作**\n• 查看当前运行节点是否卡住\n• 必要时终止并重跑，或调整基线时长"
        else:
            action = "**建议动作**\n• 先看失败节点日志\n• 确认后再重试失败节点"
        if trigger:
            action += f"\n• 触发来源：{trigger}"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": action}})
        # 点名当班人：群里不用再问「这个谁看」
        on_call = on_call_label(db, event.workspace_id)
        if on_call and on_call != "未排班":
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**当前值班**：{on_call}"},
            })
    if url:
        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": button_label},
                "type": "primary",
                "url": url,
            }],
        })
    elif kind == "test":
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "未配置站点入口，测试卡没有跳转按钮。请到 **平台集成 → 站点入口** 填写浏览器地址后再测。"},
        })
    note = f"{BRAND_SUITE} · {(ws.name if ws else '') or '告警'}"
    if kind != "test":
        note += " · 仅推送配置时刻之后的失败"
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note[:80]}]})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": template,
            },
            "elements": elements,
        },
    }


def _in_notify_cooldown(db: Session, event: AlertEvent, minutes: int) -> bool:
    if minutes <= 0 or not event.workflow_id:
        return False
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    row = (
        db.query(AlertEvent)
        .filter(
            AlertEvent.workspace_id == event.workspace_id,
            AlertEvent.workflow_id == event.workflow_id,
            AlertEvent.alert_type == event.alert_type,
            AlertEvent.notification_status.in_(("sent", "partial")),
            AlertEvent.created_at >= cutoff,
            AlertEvent.id != event.id,
        )
        .first()
    )
    return row is not None


def notify_alert_event(db: Session, event: AlertEvent, *, force: bool = False) -> dict:
    cfg = db.query(AlertNotificationConfig).filter(AlertNotificationConfig.workspace_id == event.workspace_id).first()
    if not cfg:
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
    channels = list(_enabled_channels(cfg))
    if not force:
        if not cfg.enabled and not channels:
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "disabled"}
        muted_until = getattr(cfg, "muted_until", None)
        if muted_until and datetime.utcnow() < muted_until:
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "muted"}
        if _SEVERITY_ORDER.get(severity, 2) < _SEVERITY_ORDER.get(cfg.min_severity or "error", 2):
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "severity_below_threshold"}
        if not channels:
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "no_channel"}
        cooldown = int(getattr(cfg, "notify_cooldown_minutes", None) or 15)
        # 基线破线每个业务日期只会有一条，不做冷却；失败与超时按工作流冷却防刷群
        if event.alert_type in ("failed", "timeout") and _in_notify_cooldown(db, event, cooldown):
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "cooldown"}
        if event.alert_type != "test" and _before_notify_arm(db, event, cfg):
            event.notification_status = "skipped"
            return {"sent": [], "failed": [], "skipped": "before_armed_at"}
        deferred, until = quiet_hours_decision(
            db, cfg, workspace_id=event.workspace_id, severity=severity
        )
        if deferred and until is not None:
            # 压住而不是丢掉：告警已经在告警中心，到点由重投任务推出去
            event.notification_status = "deferred"
            event.notify_pending_channels = ",".join(sorted(channels))[:256]
            event.notify_next_retry_at = until
            event.notify_last_error = None
            return {"sent": [], "failed": [], "skipped": "quiet_hours", "deferred_until": until}

    sent, failed = _deliver_channels(db, cfg, event, channels, severity)
    _record_delivery_outcome(event, sent, failed)
    return {"sent": sent, "failed": failed}


def _deliver_channels(
    db: Session, cfg: AlertNotificationConfig, event: AlertEvent, channels: list[str], severity: str
) -> tuple[list[str], list[dict]]:
    title, content = render_alert_message(db, event)
    sent: list[str] = []
    failed: list[dict] = []
    for ch in channels:
        try:
            if ch == "email":
                _send_email(cfg, title, content)
            elif ch == "webhook":
                _post_json(cfg.webhook_url, {"title": title, "content": content, "severity": severity, "alert_id": event.id})
            elif ch == "lark":
                _post_json(cfg.lark_webhook_url, _lark_card_payload(db, event, title, content))
            elif ch == "wecom":
                _post_json(cfg.wecom_webhook_url, {"msgtype": "text", "text": {"content": f"{title}\n{content}"}})
            sent.append(ch)
        except Exception as e:
            logger.warning("alert notification failed alert_id=%s channel=%s: %s", event.id, ch, e)
            failed.append({"channel": ch, "error": str(e)[:300]})
    return sent, failed


def _record_delivery_outcome(event: AlertEvent, sent: list[str], failed: list[dict]) -> None:
    """
    投递结果写回事件，失败的渠道进重试队列。
    只重投失败的那几个渠道，已经发出去的不会再刷一遍群。
    """
    event.notify_attempts = int(getattr(event, "notify_attempts", 0) or 0) + 1
    if failed:
        event.notification_status = "partial" if sent else "failed"
        event.notify_last_error = "; ".join(f"{f['channel']}: {f['error']}" for f in failed)[:500]
        pending = sorted({f["channel"] for f in failed})
        event.notify_pending_channels = ",".join(pending)[:256]
        event.notify_next_retry_at = _next_retry_at(event.notify_attempts)
        if event.notify_next_retry_at is None:
            logger.warning(
                "告警通知重试次数用尽 alert_id=%s channels=%s", event.id, event.notify_pending_channels
            )
    else:
        event.notification_status = "sent" if sent else "skipped"
        event.notify_last_error = None
        event.notify_pending_channels = None
        event.notify_next_retry_at = None


def _next_retry_at(attempts: int) -> Optional[datetime]:
    """退避 1/5/15/60 分钟，四次仍失败就停手并留在「投递失败」列表里等人处理。"""
    delays = {1: 1, 2: 5, 3: 15, 4: 60}
    minutes = delays.get(int(attempts))
    return datetime.utcnow() + timedelta(minutes=minutes) if minutes else None


def retry_pending_notifications(db: Session, *, limit: int = 50) -> dict:
    """重投到期的失败通知。由后台任务驱动，运维不需要手动补发。"""
    now = datetime.utcnow()
    due = (
        db.query(AlertEvent)
        .filter(
            # deferred 是静默时段压住的，到点和失败重投走同一条路
            AlertEvent.notification_status.in_(("failed", "partial", "deferred")),
            AlertEvent.notify_pending_channels.isnot(None),
            AlertEvent.notify_next_retry_at.isnot(None),
            AlertEvent.notify_next_retry_at <= now,
        )
        .order_by(AlertEvent.notify_next_retry_at.asc())
        .limit(int(limit))
        .all()
    )
    recovered = 0
    still_failing = 0
    for event in due:
        cfg = (
            db.query(AlertNotificationConfig)
            .filter(AlertNotificationConfig.workspace_id == event.workspace_id)
            .first()
        )
        if not cfg:
            event.notification_status = "skipped"
            event.notify_pending_channels = None
            event.notify_next_retry_at = None
            continue
        enabled = set(_enabled_channels(cfg))
        channels = [ch for ch in (event.notify_pending_channels or "").split(",") if ch and ch in enabled]
        if not channels:
            # 渠道已被关掉，不必再重投
            event.notify_pending_channels = None
            event.notify_next_retry_at = None
            continue
        severity = getattr(event, "severity", None) or event.level or "error"
        sent, failed = _deliver_channels(db, cfg, event, channels, severity)
        _record_delivery_outcome(event, sent, failed)
        if failed:
            still_failing += 1
        else:
            recovered += 1
    if due:
        db.commit()
    return {"due": len(due), "recovered": recovered, "still_failing": still_failing}

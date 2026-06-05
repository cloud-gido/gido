# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
告警服务：支持 Webhook / 邮件告警
"""
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Optional
import httpx

from app.core.brand import BRAND_SUITE

logger = logging.getLogger(__name__)


def send_alert(title: str, content: str, webhook_url: Optional[str] = None, email_to: Optional[str] = None):
    """发送告警，支持 Webhook 和邮件"""
    from app.core.config import settings
    url = webhook_url or settings.ALERT_WEBHOOK_URL
    mail = email_to or settings.ALERT_EMAIL
    if url:
        _send_webhook(title, content, url)
    if mail:
        _send_email(title, content, mail)


def _send_webhook(title: str, content: str, url: str):
    try:
        payload = {"msgtype": "text", "text": {"content": f"【{BRAND_SUITE}告警】{title}\n{content}"}}
        httpx.post(url, json=payload, timeout=5)
        logger.info(f"Webhook 告警已发送: {title}")
    except Exception as e:
        logger.warning(f"Webhook 告警发送失败: {e}")


def _send_email(title: str, content: str, to_addr: str):
    from app.core.config import settings
    if not getattr(settings, "SMTP_HOST", None):
        return
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = f"【{BRAND_SUITE}告警】{title}"
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_addr
        with smtplib.SMTP(settings.SMTP_HOST, getattr(settings, "SMTP_PORT", 25)) as s:
            s.sendmail(settings.SMTP_FROM, [to_addr], msg.as_string())
        logger.info(f"邮件告警已发送至 {to_addr}: {title}")
    except Exception as e:
        logger.warning(f"邮件告警发送失败: {e}")


def alert_workflow_failed(workflow_name: str, instance_id: int, errors: list):
    content = f"工作流: {workflow_name}\n实例ID: {instance_id}\n错误:\n" + "\n".join(errors)
    send_alert(f"工作流执行失败: {workflow_name}", content)


def alert_quality_failed(rule_name: str, score: int, threshold: str):
    content = f"规则: {rule_name}\n得分: {score}\n阈值: {threshold}"
    send_alert(f"数据质量检查失败: {rule_name}", content)

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""GIDO Stream Flink 提交模式（默认 Operator；遗留 Session/Application 由环境变量控制）。"""
from typing import Optional

from app.core.config import settings


def legacy_flink_submit_enabled() -> bool:
    return bool(settings.GIDO_LEGACY_FLINK_SUBMIT)


def default_sql_submit_mode() -> str:
    raw = (settings.GIDO_FLINK_SUBMIT_MODE or "operator").strip().lower()
    if raw in ("operator", "flink_operator"):
        return "flink_operator"
    if legacy_flink_submit_enabled():
        if raw == "session":
            return "session"
        if raw in ("application", "kubernetes_application", "k8s_application"):
            return "kubernetes_application"
    return "flink_operator"


def normalize_sql_submit_mode(mode: Optional[str]) -> str:
    s = (mode or default_sql_submit_mode()).strip().lower()
    if s not in ("session", "kubernetes_application", "flink_operator"):
        s = default_sql_submit_mode()
    if s in ("session", "kubernetes_application") and not legacy_flink_submit_enabled():
        return "flink_operator"
    return s


def enforce_sql_submit_mode_allowed(mode: str) -> str:
    s = normalize_sql_submit_mode(mode)
    raw = (mode or "").strip().lower()
    if raw in ("session", "kubernetes_application") and not legacy_flink_submit_enabled():
        raise ValueError(
            "flink_sql_submit_mode 仅支持 flink_operator；"
            "设置环境变量 GIDO_LEGACY_FLINK_SUBMIT=true 可启用 Session / K8s Application"
        )
    return s


def default_jar_submit_mode() -> str:
    return "flink_operator"


def normalize_jar_submit_mode(raw: Optional[str]) -> str:
    mode = (raw or default_jar_submit_mode()).strip().lower()
    if mode not in ("session", "flink_operator"):
        mode = default_jar_submit_mode()
    if mode == "session" and not legacy_flink_submit_enabled():
        return "flink_operator"
    return mode


def enforce_jar_submit_mode_allowed(mode: str) -> str:
    s = normalize_jar_submit_mode(mode)
    raw = (mode or "").strip().lower()
    if raw == "session" and not legacy_flink_submit_enabled():
        raise ValueError(
            "flink_jar_submit_mode 仅支持 flink_operator；"
            "设置环境变量 GIDO_LEGACY_FLINK_SUBMIT=true 可启用 Session"
        )
    return s

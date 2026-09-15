# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""调度契约：节点重试间隔 / 工作流失败策略等，发布进 Dolphin 前归一化。"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

VALID_FAILURE_STRATEGIES = frozenset({"CONTINUE", "END"})
VALID_PRIORITIES = frozenset({"HIGHEST", "HIGH", "MEDIUM", "LOW", "LOWEST"})


def _int_or_default(raw: Any, default: int, *, min_v: int = 0, max_v: int = 10_080) -> int:
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(min_v, min(max_v, v))


def fail_retry_interval_minutes(node: Mapping[str, Any] | None) -> int:
    """节点失败重试间隔（分钟）。缺省 1；显式 0 表示立即重试。"""
    raw = node.get("retry_interval_minutes") if isinstance(node, Mapping) else None
    return _int_or_default(raw, 1, min_v=0, max_v=1440)


def normalize_failure_strategy(raw: Any) -> str:
    s = str(raw or "CONTINUE").strip().upper()
    return s if s in VALID_FAILURE_STRATEGIES else "CONTINUE"


def normalize_process_priority(raw: Any) -> str:
    s = str(raw or "MEDIUM").strip().upper()
    return s if s in VALID_PRIORITIES else "MEDIUM"


def normalize_worker_group(raw: Any) -> str:
    s = str(raw or "default").strip()
    return s or "default"


def resolve_schedule_timezone(wf: Any, workspace_timezone: Optional[str] = None) -> str:
    explicit = getattr(wf, "schedule_timezone", None) if wf is not None else None
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    if workspace_timezone and str(workspace_timezone).strip():
        return str(workspace_timezone).strip()
    return "Asia/Shanghai"


def schedule_opts_from_workflow(wf: Any, *, workspace_timezone: Optional[str] = None) -> Dict[str, str]:
    """供 set_schedule / run_process 使用的流程级策略。"""
    return {
        "failure_strategy": normalize_failure_strategy(getattr(wf, "failure_strategy", None)),
        "process_priority": normalize_process_priority(getattr(wf, "process_priority", None)),
        "worker_group": normalize_worker_group(getattr(wf, "worker_group", None)),
        "timezone_id": resolve_schedule_timezone(wf, workspace_timezone),
    }

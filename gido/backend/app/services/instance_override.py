# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""人工干预实例状态（置成功/置失败）。运维用它解除下游阻塞，而不必等引擎里的那次运行真的跑通。"""

from datetime import datetime
from typing import Any, Optional

# 允许人工改写成的终态
OVERRIDE_STATUSES = ("success", "failed")


def is_status_pinned(inst: Any) -> bool:
    """置过状态的实例，引擎采集回来的状态一律不再覆盖——人工结论优先于引擎事实。"""
    return bool(getattr(inst, "status_override", None))


def apply_override(inst: Any, status: str, *, user_id: Optional[int], reason: Optional[str]) -> None:
    inst.status = status
    inst.status_override = status
    inst.override_by = user_id
    inst.override_at = datetime.utcnow()
    inst.override_reason = (reason or "").strip()[:500] or None
    if status in ("success", "failed") and inst.finished_at is None:
        inst.finished_at = inst.override_at


def clear_override(inst: Any) -> None:
    """撤销人工状态，后续采集重新以引擎为准。"""
    inst.status_override = None
    inst.override_by = None
    inst.override_at = None
    inst.override_reason = None

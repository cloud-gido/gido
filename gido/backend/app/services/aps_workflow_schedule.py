# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地 APScheduler 工作流定时策略：避免与 Dolphin 双调度。

策略（优先级从高到低）：
1. 平台库开关 / 环境变量强制关闭 → 一律不注册、不执行
2. 工作流已被 DS 托管（有 definition / engine=dolphin / 空间 DS 启用）→ 跳过该工作流
3. 其余 cron 活跃工作流才由 APS 触发（无 DS 的本地兜底）
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import PlatformIntegration, Workflow
from app.services.ds_runtime import ensure_platform_integration_row, get_dolphin_runtime


def _env_force() -> Optional[bool]:
    """APS_WORKFLOW_SCHEDULE_ENABLED：true/false；未设置则 None=自动。"""
    raw = getattr(settings, "APS_WORKFLOW_SCHEDULE_ENABLED", None)
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("", "auto", "none", "null"):
        return None
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def get_aps_workflow_override(db: Session) -> Optional[bool]:
    row = db.query(PlatformIntegration).filter(PlatformIntegration.id == 1).first()
    if row is None:
        return None
    return getattr(row, "aps_workflow_schedule_enabled", None)


def resolve_aps_workflow_master_switch(db: Session) -> Tuple[bool, str]:
    """是否允许 APS 注册/执行「未被 DS 托管」的工作流定时。

    返回 (allowed, reason)。allowed=False 时任何工作流都不走 APS。
    """
    env = _env_force()
    if env is False:
        return False, "环境变量 APS_WORKFLOW_SCHEDULE_ENABLED=false"
    db_override = get_aps_workflow_override(db)
    if db_override is False:
        return False, "平台开关：已手动关闭 APS 工作流定时"
    if env is True:
        return True, "环境变量 APS_WORKFLOW_SCHEDULE_ENABLED=true（仍跳过已发布到 Dolphin 的工作流）"
    if db_override is True:
        return True, "平台开关：已手动开启 APS 工作流定时（仍跳过已发布到 Dolphin 的工作流）"
    # auto：默认允许本地兜底；具体工作流仍按 is_workflow_aps_eligible 过滤
    return True, "自动：允许未托管给 Dolphin 的工作流使用 APS 兜底"


def is_workflow_ds_managed(db: Session, wf: Workflow) -> Tuple[bool, str]:
    """工作流是否应由 Dolphin 负责周期触发（APS 必须让路）。

    注意：模型上 ``scheduler_engine`` 默认值就是 ``dolphin``，不能单独作为「已托管」依据；
    以「已发布 definition」或「空间启用了 Dolphin」为准。
    """
    if (getattr(wf, "scheduler_definition_id", None) or "").strip():
        return True, "已有 scheduler_definition_id（已发布到生产调度）"
    status = (getattr(wf, "status", None) or "").strip().lower()
    engine = (getattr(wf, "scheduler_engine", None) or "").strip().lower()
    if status in ("published", "paused") and engine in ("dolphin", "dolphinscheduler", "ds"):
        # 已上线/暂停但偶发缺 definition_id 时仍视为 DS 侧负责
        return True, f"status={status} 且 scheduler_engine={engine}"
    try:
        if get_dolphin_runtime(db, int(wf.workspace_id)).enabled:
            return True, "工作空间已启用 DolphinScheduler"
    except Exception:
        pass
    return False, ""

def is_workflow_aps_eligible(db: Session, wf: Workflow) -> Tuple[bool, str]:
    """该工作流当前是否允许被 APS 定时触发。"""
    allowed, reason = resolve_aps_workflow_master_switch(db)
    if not allowed:
        return False, reason
    if not wf or not wf.is_active:
        return False, "工作流未激活"
    if (wf.schedule_type or "") != "cron" or not (wf.cron_expression or "").strip():
        return False, "非 cron 调度"
    managed, mreason = is_workflow_ds_managed(db, wf)
    if managed:
        return False, f"已由 Dolphin 托管：{mreason}"
    return True, "eligible"


def set_aps_workflow_override(db: Session, enabled: Optional[bool]) -> PlatformIntegration:
    """写入平台覆盖；None 表示恢复自动。"""
    row = ensure_platform_integration_row(db)
    row.aps_workflow_schedule_enabled = enabled
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def aps_workflow_status(db: Session) -> Dict[str, Any]:
    from app.services.scheduler import scheduler as apscheduler

    master_ok, master_reason = resolve_aps_workflow_master_switch(db)
    env = _env_force()
    db_override = get_aps_workflow_override(db)
    wf_jobs = [j for j in apscheduler.get_jobs() if str(j.id).startswith("wf_")]
    global_ds = get_dolphin_runtime(db).enabled
    return {
        "master_allowed": master_ok,
        "master_reason": master_reason,
        "mode": (
            "force_off"
            if (env is False or db_override is False)
            else ("force_on" if (env is True or db_override is True) else "auto")
        ),
        "env_override": env,
        "db_override": db_override,
        "global_ds_enabled": global_ds,
        "aps_workflow_job_count": len(wf_jobs),
        "aps_workflow_jobs": [
            {
                "id": j.id,
                "next_run": str(j.next_run_time) if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
            for j in wf_jobs
        ],
        "hint": (
            "生产使用 Dolphin 时请保持关闭或自动；关闭后立即清除 backend 上的 wf_* 定时任务，"
            "避免与 Dolphin 早晚双跑。"
        ),
    }

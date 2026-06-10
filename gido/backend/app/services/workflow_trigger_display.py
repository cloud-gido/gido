# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""工作流实例 trigger_type 展示：区分 Dolphin 流程实例 ID 与纯本地触发。"""

from typing import Optional


def is_manual_development_workflow_run(trigger_type: Optional[str]) -> bool:
    """
    数据开发里点「立即运行」产生的实例：纯本地 manual，或 Dolphin 上 START_PROCESS 的 manual|ds:…。
    默认不进入运维中心列表（与周期/调度运维分离）；可在接口层通过 include_manual_development_runs 打开。
    """
    if trigger_type is None:
        return False
    s = str(trigger_type).strip()
    if s == "manual":
        return True
    return s.startswith("manual|")


def parse_dolphin_process_instance_id(trigger_type: Optional[str]) -> Optional[int]:
    if not trigger_type or "|ds:" not in str(trigger_type):
        return None
    tail = str(trigger_type).rsplit("|ds:", 1)[-1].strip()
    head = tail.split("|")[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _kind_from_dolphin_command(command_upper: str) -> Optional[str]:
    """Dolphin process instance commandType → 中文触发类别；未知返回 None。"""
    if not command_upper:
        return None
    c = command_upper.replace(" ", "_")
    if "COMPLEMENT" in c:
        return "补数据"
    if any(x in c for x in ("SCHEDULER", "START_TIMER", "TIMER")):
        return "调度执行"
    if "REPEAT_RUNNING" in c or "RECOVER" in c or "RECOVERY" in c:
        return "重跑/恢复"
    if "START_PROCESS" in c or "START_CURRENT" in c or "EXECUTE" in c:
        return "开发手动启动（Dolphin 启动工作流，无调度时间）"
    return None


def format_trigger_type_label(
    trigger_type: Optional[str],
    dolphin_command_type: Optional[str] = None,
) -> str:
    """
    将库内 trigger_type（如 manual|ds:12345）格式化为运维可读文案。
    若已回填 dolphin_command_type（来自 Dolphin REST），优先按其区分定时调度与手动触发。
    """
    if trigger_type is None or not str(trigger_type).strip():
        return "—"
    raw = str(trigger_type).strip()
    ds_id = parse_dolphin_process_instance_id(raw)
    cmd_kind = _kind_from_dolphin_command((dolphin_command_type or "").upper())
    if ds_id is not None and cmd_kind:
        return f"Dolphin 流程实例 #{ds_id}（{cmd_kind}）"
    left = raw.split("|ds:", 1)[0] if "|ds:" in raw else raw
    base = (left.split("|")[0] if left else raw).strip() or "unknown"
    kind_cn = {
        "manual": "开发手动（立即运行）",
        "schedule": "周期调度",
        "rerun": "重跑",
        "batch": "补数据",
    }.get(base, base)
    if ds_id is not None:
        return f"Dolphin 流程实例 #{ds_id}（{kind_cn}）"
    if base == "schedule":
        return f"{kind_cn}（内置调度，无 Dolphin 流程实例 ID）"
    return f"{kind_cn}（本地执行，无 Dolphin 流程实例 ID）"

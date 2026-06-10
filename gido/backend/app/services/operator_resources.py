# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""Flink Operator 作业级资源配置（对标 Ververica / 实时计算 JM/TM/Slots 调优）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings


@dataclass(frozen=True)
class OperatorResources:
    jm_memory: str
    jm_cpu: float
    tm_memory: str
    tm_cpu: float
    task_slots: int
    tm_replicas: Optional[int]
    upgrade_mode: str
    flink_configuration: Dict[str, str]


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _coerce_positive_float(value: Any, default: float) -> float:
    try:
        n = float(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _coerce_memory(value: Any, default: str) -> str:
    s = str(value or "").strip()
    return s if s else default


def _parse_resource_block(block: Any, mem_key: str, cpu_key: str) -> Tuple[Optional[str], Optional[float], Optional[int]]:
    if not isinstance(block, dict):
        return None, None, None
    mem = block.get(mem_key) or block.get("memory")
    cpu = block.get(cpu_key) or block.get("cpu")
    replicas = block.get("replicas")
    mem_s = str(mem).strip() if mem is not None and str(mem).strip() else None
    cpu_f: Optional[float] = None
    if cpu is not None:
        try:
            cpu_f = float(cpu)
        except (TypeError, ValueError):
            cpu_f = None
    rep: Optional[int] = None
    if replicas is not None:
        try:
            rep = int(replicas)
            if rep < 1:
                rep = None
        except (TypeError, ValueError):
            rep = None
    return mem_s, cpu_f, rep


def resolve_operator_resources(overrides: Optional[Dict[str, Any]] = None) -> OperatorResources:
    """环境变量为默认；streaming_properties.operator_resources 按作业覆盖。"""
    ov = overrides if isinstance(overrides, dict) else {}
    jm_block = ov.get("jobManager") if isinstance(ov.get("jobManager"), dict) else {}
    tm_block = ov.get("taskManager") if isinstance(ov.get("taskManager"), dict) else {}

    jm_mem, jm_cpu, _ = _parse_resource_block(jm_block, "memory", "cpu")
    tm_mem, tm_cpu, tm_rep = _parse_resource_block(tm_block, "memory", "cpu")

    slots_raw = ov.get("taskSlots") if ov.get("taskSlots") is not None else ov.get("numberOfTaskSlots")
    upgrade = str(ov.get("upgradeMode") or settings.FLINK_OPERATOR_UPGRADE_MODE or "stateless").strip()

    flink_conf: Dict[str, str] = {}
    raw_fc = ov.get("flinkConfiguration")
    if isinstance(raw_fc, dict):
        for k, v in raw_fc.items():
            if v is None:
                continue
            flink_conf[str(k)] = v if isinstance(v, str) else str(v)

    return OperatorResources(
        jm_memory=_coerce_memory(jm_mem, settings.FLINK_OPERATOR_JM_MEMORY or "2048m"),
        jm_cpu=_coerce_positive_float(jm_cpu, float(settings.FLINK_OPERATOR_JM_CPU or 1)),
        tm_memory=_coerce_memory(tm_mem, settings.FLINK_OPERATOR_TM_MEMORY or "2048m"),
        tm_cpu=_coerce_positive_float(tm_cpu, float(settings.FLINK_OPERATOR_TM_CPU or 1)),
        task_slots=_coerce_positive_int(slots_raw, int(settings.FLINK_OPERATOR_TASK_SLOTS or 2)),
        tm_replicas=tm_rep,
        upgrade_mode=upgrade or "stateless",
        flink_configuration=flink_conf,
    )


# 资源规格模板（对标实时计算 小/中/大 规格）
OPERATOR_RESOURCE_PRESETS: Dict[str, Dict[str, Any]] = {
    "small": {
        "jobManager": {"memory": "1024m", "cpu": 0.5},
        "taskManager": {"memory": "2048m", "cpu": 1, "replicas": 1},
        "taskSlots": 2,
    },
    "medium": {
        "jobManager": {"memory": "2048m", "cpu": 1},
        "taskManager": {"memory": "4096m", "cpu": 2, "replicas": 1},
        "taskSlots": 4,
    },
    "large": {
        "jobManager": {"memory": "4096m", "cpu": 2},
        "taskManager": {"memory": "8192m", "cpu": 4, "replicas": 2},
        "taskSlots": 8,
    },
}


def _merge_operator_resource_preset(
    overrides: Dict[str, Any], tier: Optional[str]
) -> Dict[str, Any]:
    """resource_tier 为底，operator_resources 字段覆盖。"""
    t = (tier or "").strip().lower()
    base = dict(OPERATOR_RESOURCE_PRESETS.get(t, {}))
    if not overrides:
        return base
    merged = dict(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            inner = dict(merged[k])
            inner.update(v)
            merged[k] = inner
        else:
            merged[k] = v
    return merged


def split_streaming_properties_for_operator(
    extra: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Any], OperatorResources]:
    """
    从 streaming_properties 拆出 operator_resources 与其余键（可并入 flinkConfiguration）。
    顶级 k8s_application 保留给 SQL Gateway Application 路径。
    """
    if not extra:
        return {}, resolve_operator_resources(None)
    raw_or = extra.get("operator_resources")
    tier = extra.get("resource_tier")
    rest: Dict[str, Any] = {}
    for k, v in extra.items():
        if k in ("operator_resources", "k8s_application", "resource_tier", "sql_source"):
            continue
        if v is not None:
            rest[k] = v
    overrides = _merge_operator_resource_preset(
        raw_or if isinstance(raw_or, dict) else {},
        str(tier) if tier is not None else None,
    )
    return rest, resolve_operator_resources(overrides)


def merge_flink_configuration(
    base: Dict[str, str],
    resources: OperatorResources,
    extra_props: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """合并全局 checkpoint、slots、作业级 flinkConfiguration 与其余 streaming_properties 字符串键。"""
    out = dict(base)
    out["taskmanager.numberOfTaskSlots"] = str(resources.task_slots)
    out.update(resources.flink_configuration)
    if extra_props:
        for k, v in extra_props.items():
            if v is None:
                continue
            out[str(k)] = v if isinstance(v, str) else str(v)
    return out

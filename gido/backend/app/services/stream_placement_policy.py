# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic placement and capacity recommendations for Stream jobs.

This module only makes policy decisions. It never mutates a FlinkDeployment or
moves a running job; callers must separately execute approved placement changes.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Tuple


class PlacementMode(str, Enum):
    DEDICATED = "dedicated"
    GROUPED = "grouped"
    RECOMMEND_ONLY = "recommend-only"


class UnsafeRegroupError(ValueError):
    """Raised when a caller attempts to automatically move an existing job."""


@dataclass(frozen=True)
class JobPlacementRequirements:
    job_id: str
    requested_mode: PlacementMode = PlacementMode.RECOMMEND_ONLY
    sla_tier: str = "standard"
    stateful: bool = False
    state_size_gb: float = 0.0
    security_domain: str = "default"
    runtime_version: str = "2.0.1"
    checkpoint_backend: str = "filesystem"
    custom_dependencies: Tuple[str, ...] = ()
    parallelism: int = 1
    expected_records_per_second: int = 0
    existing_group_id: Optional[str] = None


@dataclass(frozen=True)
class PlacementGroup:
    group_id: str
    security_domain: str = "default"
    runtime_version: str = "2.0.1"
    checkpoint_backend: str = "filesystem"
    custom_dependencies: Tuple[str, ...] = ()
    capacity_slots: int = 1
    used_slots: int = 0
    allows_stateful: bool = True
    highest_sla_tier: str = "standard"

    @property
    def available_slots(self) -> int:
        return max(0, self.capacity_slots - self.used_slots)


@dataclass(frozen=True)
class ResourceRecommendation:
    task_slots: int
    taskmanager_replicas: int
    cpu_cores: float
    memory_mb: int
    checkpoint_storage_gb: int
    headroom_percent: int
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PlacementDecision:
    mode: PlacementMode
    target_group_id: Optional[str]
    auto_apply: bool
    safe_to_regroup: bool
    reason_codes: Tuple[str, ...]
    resources: ResourceRecommendation
    considered_groups: Tuple[str, ...] = ()


_SLA_FACTORS = {"best-effort": 1.0, "standard": 1.25, "high": 1.5, "critical": 2.0}
_SLA_ORDER = {"best-effort": 0, "standard": 1, "high": 2, "critical": 3}


def recommend_resources(requirements: JobPlacementRequirements) -> ResourceRecommendation:
    """Produce a stable initial capacity estimate, not an autoscaling promise."""
    sla = requirements.sla_tier.strip().lower()
    factor = _SLA_FACTORS.get(sla, _SLA_FACTORS["standard"])
    throughput_slots = math.ceil(max(0, requirements.expected_records_per_second) / 20_000)
    slots = max(1, requirements.parallelism, throughput_slots)
    slots = max(slots, math.ceil(slots * factor))
    state_memory = math.ceil(max(0.0, requirements.state_size_gb) * 64)
    memory_mb = int(math.ceil((1024 + slots * 768 + state_memory) / 256) * 256)
    checkpoint_gb = math.ceil(max(1.0, requirements.state_size_gb * (3.0 if requirements.stateful else 1.0)))
    headroom = int(round((factor - 1.0) * 100))
    reasons = ["parallelism_and_throughput"]
    if requirements.stateful:
        reasons.append("state_and_checkpoint_overhead")
    if sla in {"high", "critical"}:
        reasons.append(f"sla_{sla}_headroom")
    return ResourceRecommendation(
        task_slots=slots,
        taskmanager_replicas=max(1, math.ceil(slots / 4)),
        cpu_cores=float(slots),
        memory_mb=memory_mb,
        checkpoint_storage_gb=checkpoint_gb,
        headroom_percent=headroom,
        reasons=tuple(reasons),
    )


def _compatible_group(
    requirements: JobPlacementRequirements,
    group: PlacementGroup,
    required_slots: int,
) -> Tuple[bool, Tuple[str, ...]]:
    reasons = []
    if group.security_domain != requirements.security_domain:
        reasons.append("security_domain_mismatch")
    if group.runtime_version != requirements.runtime_version:
        reasons.append("runtime_version_mismatch")
    if group.checkpoint_backend != requirements.checkpoint_backend:
        reasons.append("checkpoint_backend_mismatch")
    if tuple(sorted(group.custom_dependencies)) != tuple(sorted(requirements.custom_dependencies)):
        reasons.append("custom_dependencies_mismatch")
    if requirements.stateful and not group.allows_stateful:
        reasons.append("stateful_not_supported")
    if group.available_slots < required_slots:
        reasons.append("insufficient_capacity")
    if _SLA_ORDER.get(group.highest_sla_tier, 1) >= _SLA_ORDER["critical"]:
        reasons.append("critical_sla_group_isolated")
    return not reasons, tuple(reasons)


def _requires_dedicated(requirements: JobPlacementRequirements) -> Tuple[str, ...]:
    reasons = []
    if requirements.sla_tier.strip().lower() == "critical":
        reasons.append("critical_sla")
    if requirements.security_domain.strip().lower() in {"restricted", "regulated", "pci", "pii"}:
        reasons.append("security_isolation")
    if requirements.custom_dependencies:
        reasons.append("custom_dependency_isolation")
    if requirements.stateful and requirements.state_size_gb >= 100:
        reasons.append("large_state_isolation")
    return tuple(reasons)


def decide_placement(
    requirements: JobPlacementRequirements,
    groups: Iterable[PlacementGroup] = (),
) -> PlacementDecision:
    """Select placement deterministically; group iteration order cannot change the result."""
    group_list = tuple(groups)
    resources = recommend_resources(requirements)
    isolation_reasons = _requires_dedicated(requirements)
    considered = tuple(sorted(group.group_id for group in group_list))

    if requirements.requested_mode == PlacementMode.DEDICATED or isolation_reasons:
        reasons = isolation_reasons or ("dedicated_requested",)
        return PlacementDecision(
            mode=PlacementMode.DEDICATED,
            target_group_id=None,
            auto_apply=requirements.existing_group_id is None,
            safe_to_regroup=requirements.existing_group_id is None,
            reason_codes=reasons + (("existing_job_requires_manual_migration",) if requirements.existing_group_id else ()),
            resources=resources,
            considered_groups=considered,
        )

    compatible = [
        group
        for group in group_list
        if _compatible_group(requirements, group, resources.task_slots)[0]
    ]
    target = min(compatible, key=lambda group: (group.used_slots, group.group_id)) if compatible else None
    recommended_mode = PlacementMode.GROUPED if target else PlacementMode.DEDICATED
    reason = "compatible_group_selected" if target else "no_compatible_group"

    if requirements.requested_mode == PlacementMode.RECOMMEND_ONLY:
        return PlacementDecision(
            mode=recommended_mode,
            target_group_id=target.group_id if target else None,
            auto_apply=False,
            safe_to_regroup=False,
            reason_codes=(reason, "recommend_only_no_mutation"),
            resources=resources,
            considered_groups=considered,
        )

    moving = bool(requirements.existing_group_id and requirements.existing_group_id != (target.group_id if target else None))
    return PlacementDecision(
        mode=recommended_mode,
        target_group_id=target.group_id if target else None,
        auto_apply=not moving,
        safe_to_regroup=not moving,
        reason_codes=(reason,) + (("unsafe_auto_regroup_forbidden",) if moving else ()),
        resources=resources,
        considered_groups=considered,
    )


def assert_auto_regroup_allowed(
    requirements: JobPlacementRequirements,
    decision: PlacementDecision,
) -> None:
    """Fail closed before an API or reconciler applies a placement change."""
    target = decision.target_group_id if decision.mode == PlacementMode.GROUPED else None
    moving = bool(requirements.existing_group_id and requirements.existing_group_id != target)
    if moving or not decision.auto_apply or not decision.safe_to_regroup:
        raise UnsafeRegroupError(
            "automatic regroup is forbidden; use an explicit stop/checkpoint/migrate/restart workflow"
        )

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-11
"""Flink Operator FlinkDeployment podTemplate 调度片段（nodeSelector / tolerations）。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.core.config import settings


def operator_runtime_pod_template() -> Dict[str, Any]:
    """GHCR 可变 tag（dev）须 Always 拉取，避免节点缓存旧 gido-flink-runtime。"""
    return {
        "spec": {
            "containers": [
                {
                    "name": "flink-main-container",
                    "imagePullPolicy": "Always",
                }
            ]
        }
    }


def operator_paimon_warehouse_pod_template() -> Optional[Dict[str, Any]]:
    """
    file:// Paimon warehouse 须 JM/TM 共享卷；/tmp 各 Pod 独立会导致 commit 找不到 schema。
    本地 K3s：kubectl apply -f k8s/paimon-warehouse-pvc.yaml
    """
    wh = (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip().lower()
    if not wh.startswith("file://"):
        return None
    pvc = (settings.FLINK_OPERATOR_PAIMON_PVC or "").strip()
    if not pvc:
        return None
    mount = (settings.FLINK_OPERATOR_PAIMON_WAREHOUSE_MOUNT or "/opt/flink/paimon-warehouse").strip()
    return {
        "spec": {
            "containers": [
                {
                    "name": "flink-main-container",
                    "volumeMounts": [
                        {
                            "name": "paimon-warehouse",
                            "mountPath": mount,
                        }
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "paimon-warehouse",
                    "persistentVolumeClaim": {"claimName": pvc},
                }
            ],
        }
    }


def operator_scheduling_pod_template() -> Optional[Dict[str, Any]]:
    """
    当 FLINK_OPERATOR_NODE_POOL 配置时，生成 podTemplate.spec 调度片段。
    默认 taint/nodeSelector 键：node.gamelinelab.com/pool（可用 FLINK_OPERATOR_NODE_SELECTOR_KEY 覆盖）。
    """
    pool = (settings.FLINK_OPERATOR_NODE_POOL or "").strip()
    if not pool:
        return None
    key = (settings.FLINK_OPERATOR_NODE_SELECTOR_KEY or "node.gamelinelab.com/pool").strip()
    effect = (settings.FLINK_OPERATOR_TAINT_EFFECT or "NoSchedule").strip() or "NoSchedule"
    return {
        "spec": {
            "nodeSelector": {key: pool},
            "tolerations": [
                {
                    "key": key,
                    "operator": "Equal",
                    "value": pool,
                    "effect": effect,
                }
            ],
        }
    }


def _merge_containers(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name = {c.get("name"): copy.deepcopy(c) for c in base if c.get("name")}
    for c in extra:
        name = c.get("name")
        if not name:
            continue
        if name in by_name:
            existing = by_name[name]
            for mk, mv in c.items():
                if mk == "volumeMounts" and isinstance(mv, list):
                    mounts = {m.get("name"): m for m in existing.get("volumeMounts") or [] if m.get("name")}
                    for m in mv:
                        if m.get("name"):
                            mounts[m["name"]] = m
                    existing["volumeMounts"] = list(mounts.values())
                else:
                    existing[mk] = copy.deepcopy(mv)
        else:
            by_name[name] = copy.deepcopy(c)
    return list(by_name.values())


def _merge_volumes(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_name = {v.get("name"): copy.deepcopy(v) for v in base if v.get("name")}
    for v in extra:
        name = v.get("name")
        if name:
            by_name[name] = copy.deepcopy(v)
    return list(by_name.values())


def _deep_merge_pod_template(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for key, value in src.items():
        if key == "spec" and isinstance(value, dict):
            spec_dst = dst.setdefault("spec", {})
            for sk, sv in value.items():
                if sk == "containers" and isinstance(sv, list):
                    spec_dst["containers"] = _merge_containers(spec_dst.get("containers") or [], sv)
                elif sk == "volumes" and isinstance(sv, list):
                    spec_dst["volumes"] = _merge_volumes(spec_dst.get("volumes") or [], sv)
                elif isinstance(sv, dict) and isinstance(spec_dst.get(sk), dict):
                    nested = copy.deepcopy(spec_dst[sk])
                    nested.update(copy.deepcopy(sv))
                    spec_dst[sk] = nested
                else:
                    spec_dst[sk] = copy.deepcopy(sv)
        else:
            dst[key] = copy.deepcopy(value)


def merge_pod_templates(*parts: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """合并多个 FlinkDeployment podTemplate（调度 + SQL ConfigMap 挂载等）。"""
    merged: Dict[str, Any] = {}
    for part in parts:
        if part:
            _deep_merge_pod_template(merged, part)
    return merged or None

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-11
"""Flink Operator FlinkDeployment podTemplate 调度片段（nodeSelector / tolerations）。"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from app.core.config import settings


def _parse_image_pull_secret_names(raw: Optional[str]) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [s.strip() for s in str(raw).replace(";", ",").split(",") if s.strip()]


def operator_image_pull_secrets_pod_template(
    image_pull_secrets: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """私有仓库 gido-flink-runtime 拉取凭据（podTemplate.spec.imagePullSecrets）。"""
    raw = image_pull_secrets
    if raw is None:
        raw = settings.FLINK_OPERATOR_IMAGE_PULL_SECRETS
    names = _parse_image_pull_secret_names(raw)
    if not names:
        return None
    return {
        "spec": {
            "imagePullSecrets": [{"name": n} for n in names],
        }
    }


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


def operator_jar_staging_pod_template(http_jar_url: str) -> Dict[str, Any]:
    """
    Flink 1.17 Application 模式仅接受 local:// 主 JAR：init 容器从 presigned HTTPS（或 GIDO HTTP）下载到 emptyDir，
    JM/TM 以 local:///…/job.jar 启动。
    """
    mount = (settings.FLINK_OPERATOR_JAR_STAGING_MOUNT or "/opt/flink/usrlib/gido-artifacts").strip()
    vol = "gido-job-artifact"
    init_image = (settings.FLINK_OPERATOR_JAR_STAGING_INIT_IMAGE or "curlimages/curl:8.5.0").strip()
    return {
        "spec": {
            "initContainers": [
                {
                    "name": "gido-jar-fetch",
                    "image": init_image,
                    "env": [{"name": "GIDO_JAR_URL", "value": http_jar_url}],
                    "command": [
                        "sh",
                        "-c",
                        f'mkdir -p "{mount}" && curl -fsSL "$GIDO_JAR_URL" -o "{mount}/job.jar"',
                    ],
                    "volumeMounts": [{"name": vol, "mountPath": mount}],
                }
            ],
            "containers": [
                {
                    "name": "flink-main-container",
                    "volumeMounts": [{"name": vol, "mountPath": mount, "readOnly": True}],
                }
            ],
            "volumes": [{"name": vol, "emptyDir": {}}],
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
                elif mk == "env" and isinstance(mv, list):
                    env_map = {
                        item.get("name"): copy.deepcopy(item)
                        for item in (existing.get("env") or [])
                        if isinstance(item, dict) and item.get("name")
                    }
                    for item in mv:
                        if isinstance(item, dict) and item.get("name"):
                            env_map[item["name"]] = copy.deepcopy(item)
                    existing["env"] = list(env_map.values())
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
                elif sk == "initContainers" and isinstance(sv, list):
                    spec_dst["initContainers"] = _merge_containers(
                        spec_dst.get("initContainers") or [], sv
                    )
                elif sk == "volumes" and isinstance(sv, list):
                    spec_dst["volumes"] = _merge_volumes(spec_dst.get("volumes") or [], sv)
                elif sk == "imagePullSecrets" and isinstance(sv, list):
                    by_name = {
                        item.get("name"): copy.deepcopy(item)
                        for item in (spec_dst.get("imagePullSecrets") or [])
                        if isinstance(item, dict) and item.get("name")
                    }
                    for item in sv:
                        if isinstance(item, dict) and item.get("name"):
                            by_name[item["name"]] = copy.deepcopy(item)
                    spec_dst["imagePullSecrets"] = list(by_name.values())
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

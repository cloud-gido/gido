# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""SQL 脚本制品：持久化 + ConfigMap 挂载（Operator SQL Runner 业界推荐路径）。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.services.gido_deployment_meta import sql_configmap_name
from app.services.jar_artifact import artifact_dir_for_job, resolved_artifact_download_token
from app.services.flink_operator_submit import _load_k8s_config

logger = logging.getLogger(__name__)

SQL_SCRIPT_FILENAME = "artifact.sql"
SQL_MOUNT_DIR = "/opt/flink/gido-scripts"
SQL_MOUNT_PATH = f"{SQL_MOUNT_DIR}/{SQL_SCRIPT_FILENAME}"


def sql_script_file_path(job_id: int) -> Path:
    return artifact_dir_for_job(job_id) / SQL_SCRIPT_FILENAME


def save_sql_script(job_id: int, content: str) -> Path:
    path = sql_script_file_path(job_id)
    path.write_text(content or "", encoding="utf-8")
    return path


def sql_script_exists(job_id: int) -> bool:
    p = sql_script_file_path(job_id)
    return p.is_file() and p.stat().st_size > 0


def configmap_name_for_job(job_id: int, workspace_id: int) -> str:
    return sql_configmap_name(int(workspace_id), int(job_id))


def build_sql_http_uri_for_operator(job_id: int) -> str:
    from urllib.parse import quote

    from app.core.config import settings

    base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("未配置 FLINK_OPERATOR_JAR_HTTP_BASE（SQL 制品 HTTP 基址与 JAR 共用）。")
    token = quote(resolved_artifact_download_token(), safe="")
    return f"{base}/api/streaming/jobs/{int(job_id)}/artifact.sql?token={token}"


def ensure_sql_script_configmap(
    job_id: int, workspace_id: int, sql_content: str, namespace: str
) -> str:
    """创建/更新 SQL ConfigMap，供 FlinkDeployment podTemplate 挂载。"""
    from kubernetes import client  # type: ignore

    _load_k8s_config()
    v1 = client.CoreV1Api()
    name = configmap_name_for_job(job_id, workspace_id)
    body = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(
            name=name,
            labels={
                "app.kubernetes.io/managed-by": "gido",
                "gido.io/job-type": "sql",
                "gido.io/job-id": str(int(job_id)),
                "gido.io/workspace-id": str(int(workspace_id)),
            },
        ),
        data={SQL_SCRIPT_FILENAME: sql_content or ""},
    )
    try:
        v1.create_namespaced_config_map(namespace=namespace, body=body)
        logger.info("已创建 SQL ConfigMap %s/%s", namespace, name)
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        if not isinstance(e, ApiException) or getattr(e, "status", None) != 409:
            raise
        v1.replace_namespaced_config_map(name=name, namespace=namespace, body=body)
        logger.info("已更新 SQL ConfigMap %s/%s", namespace, name)
    return name


def delete_sql_script_configmap(job_id: int, workspace_id: int, namespace: str) -> None:
    from kubernetes import client  # type: ignore

    _load_k8s_config()
    v1 = client.CoreV1Api()
    name = configmap_name_for_job(job_id, workspace_id)
    try:
        v1.delete_namespaced_config_map(name=name, namespace=namespace)
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(e, ApiException) and getattr(e, "status", None) == 404:
            return
        logger.debug("删除 SQL ConfigMap %s 失败: %s", name, e)

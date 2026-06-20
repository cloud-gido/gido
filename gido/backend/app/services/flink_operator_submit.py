# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
"""通过 Flink Kubernetes Operator 提交 JAR Application（FlinkDeployment CR）。"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.jar_artifact import resolve_jar_submit_artifacts, resolve_jar_uri_for_operator
from app.services.artifact_s3 import artifact_s3_enabled
from app.services.gido_deployment_meta import (
    GidoDeploymentMeta,
    jar_deployment_name,
    sql_deployment_name,
)
from app.services.flink_pod_scheduling import (
    merge_pod_templates,
    operator_image_pull_secrets_pod_template,
    operator_jar_staging_pod_template,
    operator_paimon_warehouse_pod_template,
    operator_runtime_pod_template,
    operator_scheduling_pod_template,
)
from app.services.operator_resources import (
    OperatorResources,
    merge_flink_configuration,
    resolve_operator_resources,
)
from app.services.s3_auth import (
    apply_flink_s3_flink_conf,
    operator_s3_credentials_pod_template,
    validate_s3_auth_for_submit,
)
from app.services.operator_runtime import OperatorRuntimeContext

logger = logging.getLogger(__name__)

FLINK_DEPLOYMENT_GROUP = "flink.apache.org"
FLINK_DEPLOYMENT_VERSION = "v1beta1"
FLINK_DEPLOYMENT_PLURAL = "flinkdeployments"


def deployment_name_for_job(job_id: int, workspace_id: Optional[int] = None) -> str:
    ws = int(workspace_id) if workspace_id is not None else 0
    return jar_deployment_name(ws, int(job_id))


def sql_deployment_name_for_job(job_id: int, workspace_id: Optional[int] = None) -> str:
    ws = int(workspace_id) if workspace_id is not None else 0
    return sql_deployment_name(ws, int(job_id))


def deployment_name_for_streaming_job(
    job_id: int, job_type: str, workspace_id: Optional[int] = None
) -> str:
    if (job_type or "").upper() == "SQL":
        return sql_deployment_name_for_job(job_id, workspace_id)
    return deployment_name_for_job(job_id, workspace_id)


def _operator_namespace() -> str:
    ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    return ns or "flink"


def kubernetes_api_available() -> bool:
    """Backend 能否访问 K8s API：集群内 ServiceAccount 或可读 kubeconfig 文件。"""
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    return bool(kc and os.path.isfile(kc))


def operator_submit_ready(
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Tuple[bool, str]:
    """Operator 一键提交前置：K8s API + 命名空间 + 制品（S3 或 HTTP）+ S3 认证。"""
    if not kubernetes_api_available():
        return False, (
            "Flink Operator 需要 Kubernetes 访问能力："
            "生产请将 gido-backend 部署在集群内（ServiceAccount + RBAC）；"
            "本机 Kind 开发请在 .env 启用 kind-local 配置并挂载 kubeconfig。"
        )
    if not _operator_namespace():
        return False, "请配置 FLINK_OPERATOR_NAMESPACE（或 FLINK_K8S_NAMESPACE）。"
    ok, s3_msg = validate_s3_auth_for_submit(runtime_ctx)
    if not ok:
        return False, s3_msg
    from app.services.artifact_s3 import artifact_s3_enabled, s3_prefix_config_hint

    if artifact_s3_enabled(runtime_ctx):
        hint = s3_prefix_config_hint(runtime_ctx)
        if hint:
            return False, hint
        return True, ""
    if settings.GIDO_JAR_ARTIFACT_REQUIRE_S3:
        return False, (
            s3_prefix_config_hint(runtime_ctx)
            or "生产环境须配置 Operator 集群 JAR 制品 S3 前缀（GIDO_JAR_ARTIFACT_REQUIRE_S3=true）。"
        )
    if not (settings.FLINK_OPERATOR_ARTIFACT_TOKEN or "").strip():
        return False, "请配置 FLINK_OPERATOR_ARTIFACT_TOKEN（HTTP 制品拉取校验）。"
    jar_base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip()
    if not jar_base and not (settings.FLINK_OPERATOR_JAR_S3_PREFIX or "").strip():
        return False, (
            "请配置 FLINK_OPERATOR_JAR_HTTP_BASE（集群内如 http://backend.gido.svc.cluster.local:8001）"
            "或 FLINK_OPERATOR_JAR_S3_PREFIX / Operator 集群 JAR 制品 S3 前缀。"
        )
    return True, ""


def _load_k8s_config(runtime_ctx: Optional[OperatorRuntimeContext] = None) -> None:
    from kubernetes import config  # type: ignore

    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    kc = (ctx.kubeconfig_path or settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    k8s_ctx = (ctx.k8s_context or settings.FLINK_K8S_CONTEXT or "").strip() or None
    if kc:
        config.load_kube_config(config_file=kc, context=k8s_ctx)
        return
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config(context=k8s_ctx)


def _custom_objects_api(runtime_ctx: Optional[OperatorRuntimeContext] = None):
    from kubernetes import client, config  # type: ignore

    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    kc = (ctx.kubeconfig_path or settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    k8s_ctx = (ctx.k8s_context or settings.FLINK_K8S_CONTEXT or "").strip() or None
    configuration = client.Configuration()
    if kc:
        config.load_kube_config(config_file=kc, context=k8s_ctx, client_configuration=configuration)
    else:
        try:
            config.load_incluster_config(client_configuration=configuration)
        except Exception:
            config.load_kube_config(context=k8s_ctx, client_configuration=configuration)
    return client.CustomObjectsApi(client.ApiClient(configuration))


def _parse_program_args(program_args: Optional[str]) -> List[str]:
    if not program_args or not str(program_args).strip():
        return []
    return str(program_args).split()


def _resolve_savepoint_dir(checkpoint_dir: str) -> str:
    """savepoint 路径；显式配置优先，否则由 checkpoint 路径推导（flink-checkpoints → flink-savepoints）。"""
    explicit = (settings.FLINK_OPERATOR_SAVEPOINT_DIR or "").strip()
    if explicit:
        return explicit
    ckpt = checkpoint_dir.rstrip("/")
    if ckpt.endswith("flink-checkpoints"):
        return ckpt[: -len("flink-checkpoints")] + "flink-savepoints"
    return f"{ckpt}/savepoints"


def _base_flink_conf(
    *,
    enable_http_artifacts: bool = False,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, str]:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    flink_conf: Dict[str, str] = {}
    if enable_http_artifacts:
        flink_conf["user.artifacts.raw-http-enabled"] = "true"
    ckpt = (ctx.checkpoint_dir or settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip()
    if ckpt:
        flink_conf["state.checkpoints.dir"] = ckpt
        flink_conf["execution.checkpointing.interval"] = (
            settings.FLINK_OPERATOR_CHECKPOINT_INTERVAL or "60s"
        )
        flink_conf["execution.checkpointing.savepoint-dir"] = _resolve_savepoint_dir(ckpt)
    apply_flink_s3_flink_conf(flink_conf, runtime_ctx=ctx)
    rest_ex = (settings.FLINK_K8S_REST_EXPOSED_TYPE or "LoadBalancer").strip()
    if rest_ex:
        flink_conf["kubernetes.rest-service.exposed.type"] = rest_ex
    return flink_conf


def _operator_image(runtime_ctx: Optional[OperatorRuntimeContext] = None) -> str:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    return ctx.image


def _build_pod_template_for_sql_configmap(configmap_name: str) -> Dict[str, Any]:
    from app.services.sql_artifact import SQL_MOUNT_DIR

    return {
        "spec": {
            "containers": [
                {
                    "name": "flink-main-container",
                    "volumeMounts": [
                        {
                            "name": "gido-sql-script",
                            "mountPath": SQL_MOUNT_DIR,
                            "readOnly": True,
                        }
                    ],
                }
            ],
            "volumes": [
                {
                    "name": "gido-sql-script",
                    "configMap": {"name": configmap_name},
                }
            ],
        }
    }


def build_flink_deployment_body(
    *,
    deployment_name: str,
    namespace: str,
    jar_uri: str,
    entry_class: str,
    parallelism: int,
    program_args: Optional[str] = None,
    operator_resources: Optional[OperatorResources] = None,
    job_type_label: str = "jar",
    pod_template: Optional[Dict[str, Any]] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    jar_http_fetch_url: Optional[str] = None,
) -> Dict[str, Any]:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    resources = operator_resources or resolve_operator_resources(None)
    image = _operator_image(ctx)
    flink_version = ctx.flink_version
    sa = ctx.service_account

    flink_conf = merge_flink_configuration(
        _base_flink_conf(enable_http_artifacts=jar_uri.startswith(("http://", "https://")), runtime_ctx=ctx),
        resources,
        extra_flink_props,
    )

    job_spec: Dict[str, Any] = {
        "jarURI": jar_uri,
        "entryClass": entry_class,
        "parallelism": max(1, int(parallelism or 1)),
        "upgradeMode": resources.upgrade_mode,
        "state": "running",
    }
    args = _parse_program_args(program_args)
    if args:
        job_spec["args"] = args

    jm_spec: Dict[str, Any] = {
        "resource": {"memory": resources.jm_memory, "cpu": resources.jm_cpu},
    }
    tm_spec: Dict[str, Any] = {
        "resource": {"memory": resources.tm_memory, "cpu": resources.tm_cpu},
    }
    if resources.tm_replicas is not None and resources.tm_replicas > 0:
        tm_spec["replicas"] = int(resources.tm_replicas)

    spec: Dict[str, Any] = {
        "image": image,
        "flinkVersion": flink_version,
        "serviceAccount": sa,
        "flinkConfiguration": flink_conf,
        "jobManager": jm_spec,
        "taskManager": tm_spec,
        "job": job_spec,
    }
    staging_tpl = None
    fetch_url = jar_http_fetch_url
    if fetch_url and (jar_uri or "").startswith("local://"):
        staging_tpl = operator_jar_staging_pod_template(fetch_url)
    merged_pod_template = merge_pod_templates(
        operator_runtime_pod_template(),
        operator_image_pull_secrets_pod_template(ctx.image_pull_secrets),
        operator_paimon_warehouse_pod_template(),
        operator_s3_credentials_pod_template(ctx),
        operator_scheduling_pod_template(),
        staging_tpl,
        pod_template,
    )
    if merged_pod_template:
        spec["podTemplate"] = merged_pod_template
    elif "podTemplate" in spec:
        del spec["podTemplate"]

    body: Dict[str, Any] = {
        "apiVersion": f"{FLINK_DEPLOYMENT_GROUP}/{FLINK_DEPLOYMENT_VERSION}",
        "kind": "FlinkDeployment",
        "metadata": {
            "name": deployment_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "gido",
                "gido.io/job-type": job_type_label,
            },
        },
        "spec": spec,
    }
    if deployment_meta is not None:
        deployment_meta.apply_to_body(body)
    return body


def build_flink_deployment_body_for_sql(
    *,
    deployment_name: str,
    namespace: str,
    sql_script_path: str,
    parallelism: int,
    configmap_name: str,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    pod_template: Optional[Dict[str, Any]] = None,
    enable_http_artifacts: bool = False,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    jar_uri = (settings.FLINK_OPERATOR_SQL_RUNNER_JAR_URI or "").strip()
    if not jar_uri:
        raise RuntimeError(
            "未配置 FLINK_OPERATOR_SQL_RUNNER_JAR_URI。"
            "SQL Operator 须使用含 sql-runner.jar 的 GIDO Flink 镜像。"
        )
    entry_class = (settings.FLINK_OPERATOR_SQL_RUNNER_ENTRY_CLASS or "com.gido.flink.SqlRunner").strip()
    if pod_template is None and configmap_name:
        pod_template = _build_pod_template_for_sql_configmap(configmap_name)
    resources = operator_resources or resolve_operator_resources(None)
    flink_conf = merge_flink_configuration(
        _base_flink_conf(enable_http_artifacts=enable_http_artifacts, runtime_ctx=ctx),
        resources,
        extra_flink_props,
    )

    body = build_flink_deployment_body(
        deployment_name=deployment_name,
        namespace=namespace,
        jar_uri=jar_uri,
        entry_class=entry_class,
        parallelism=parallelism,
        operator_resources=resources,
        job_type_label="sql",
        pod_template=pod_template,
        deployment_meta=deployment_meta,
        runtime_ctx=ctx,
    )
    body["spec"]["job"]["args"] = [sql_script_path]
    body["spec"]["flinkConfiguration"] = flink_conf
    return body


def sql_operator_submit_ready() -> Tuple[bool, str]:
    ok, reason = operator_submit_ready()
    if not ok:
        return ok, reason
    jar_uri = (settings.FLINK_OPERATOR_SQL_RUNNER_JAR_URI or "").strip()
    if not jar_uri:
        return False, (
            "SQL Operator 须配置 FLINK_OPERATOR_SQL_RUNNER_JAR_URI，"
            "且 FLINK_OPERATOR_IMAGE 须包含 sql-runner.jar（参考 Flink Operator flink-sql-runner-example）。"
        )
    return True, ""


def resolve_jar_uri_for_job(
    job_id: int,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    *,
    jar_path: Optional[str] = None,
) -> str:
    return resolve_jar_submit_artifacts(
        job_id, runtime_ctx=runtime_ctx, jar_path=jar_path
    ).jar_uri


def effective_sql_source(
    sql_source: Optional[str],
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> str:
    """S3 制品前缀已配置时，默认 sql_source=s3（EKS 生产，避免仅依赖 ConfigMap）。"""
    source = (sql_source or "mount").strip().lower()
    if source == "mount" and artifact_s3_enabled(runtime_ctx):
        return "s3"
    return source


def apply_flink_deployment(
    body: Dict[str, Any],
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    api = _custom_objects_api(runtime_ctx)
    meta = body.get("metadata") or {}
    name = meta["name"]
    namespace = meta["namespace"]
    try:
        return api.create_namespaced_custom_object(
            group=FLINK_DEPLOYMENT_GROUP,
            version=FLINK_DEPLOYMENT_VERSION,
            namespace=namespace,
            plural=FLINK_DEPLOYMENT_PLURAL,
            body=body,
        )
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        if not isinstance(e, ApiException) or getattr(e, "status", None) != 409:
            raise
        existing = api.get_namespaced_custom_object(
            group=FLINK_DEPLOYMENT_GROUP,
            version=FLINK_DEPLOYMENT_VERSION,
            namespace=namespace,
            plural=FLINK_DEPLOYMENT_PLURAL,
            name=name,
        )
        em = existing.get("metadata") or {}
        body_meta = body.setdefault("metadata", {})
        if em.get("resourceVersion"):
            body_meta["resourceVersion"] = em["resourceVersion"]
        if em.get("uid"):
            body_meta["uid"] = em["uid"]
        return api.replace_namespaced_custom_object(
            group=FLINK_DEPLOYMENT_GROUP,
            version=FLINK_DEPLOYMENT_VERSION,
            namespace=namespace,
            plural=FLINK_DEPLOYMENT_PLURAL,
            name=name,
            body=body,
        )


def read_flink_deployment(
    deployment_name: str,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    api = _custom_objects_api(runtime_ctx)
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    return api.get_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        name=deployment_name,
    )


def list_flink_deployments(
    *,
    namespace: Optional[str] = None,
    workspace_id: Optional[int] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> List[Dict[str, Any]]:
    """列出命名空间内 FlinkDeployment CR；可选按 gido.io/workspace-id 标签过滤。"""
    api = _custom_objects_api(runtime_ctx)
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    kwargs: Dict[str, Any] = {}
    if workspace_id is not None:
        kwargs["label_selector"] = f"gido.io/workspace-id={int(workspace_id)}"
    resp = api.list_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        **kwargs,
    )
    items = resp.get("items") if isinstance(resp, dict) else None
    return list(items) if isinstance(items, list) else []


def deployment_summary_from_cr(cr: Dict[str, Any]) -> Dict[str, Any]:
    """FlinkDeployment CR → 运维概览行（含 JM/TM 健康摘要）。"""
    meta = cr.get("metadata") or {}
    status = cr.get("status") or {}
    spec = cr.get("spec") or {}
    labels = meta.get("labels") or {}
    jid, lifecycle, err = extract_status_from_cr(cr)
    job_status = status.get("jobStatus") or {}
    spec_state = (spec.get("job") or {}).get("state")
    spec_state = str(spec_state).strip().lower() if spec_state else None
    cluster = status.get("clusterInfo") or status.get("cluster") or {}
    jm_status = cluster.get("jobManagerStatus") or status.get("jobManagerDeploymentStatus")
    tm_status = cluster.get("taskManagerStatus") or status.get("taskManager")
    lifecycle_up = (lifecycle or "").strip().upper()
    health = "unknown"
    if spec_state == "suspended":
        health = "suspended"
    elif err or lifecycle_up in ("FAILED", "FAILING"):
        health = "failed"
    elif lifecycle_up in ("STABLE", "DEPLOYED", "RUNNING", "CREATED"):
        health = "healthy"
    elif lifecycle_up in ("DEPLOYING", "STARTING", "RECONCILING", "APPLICATION_PENDING_JOB_ID"):
        health = "starting"
    return {
        "name": meta.get("name"),
        "namespace": meta.get("namespace") or _operator_namespace(),
        "workspace_id": labels.get("gido.io/workspace-id"),
        "job_id": labels.get("gido.io/job-id"),
        "job_type": labels.get("gido.io/job-type"),
        "lifecycle": lifecycle,
        "health": health,
        "flink_job_id": jid,
        "error": err or job_status.get("error"),
        "spec_state": spec_state,
        "image": spec.get("image"),
        "flink_version": spec.get("flinkVersion"),
        "job_manager_status": jm_status,
        "task_manager_status": tm_status,
        "created_at": meta.get("creationTimestamp"),
    }


def operator_overview_payload(*, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """Flink Operator 运维概览：运行时配置 + K8s FlinkDeployment 列表与汇总。"""
    from app.services.flink_runtime_catalog import flink_runtime_api_payload

    runtime = flink_runtime_api_payload()
    ns = runtime.get("operator_namespace") or _operator_namespace()
    ready, ready_reason = operator_submit_ready()
    deployments: List[Dict[str, Any]] = []
    k8s_error: Optional[str] = None
    if kubernetes_api_available():
        try:
            crs = list_flink_deployments(namespace=ns, workspace_id=workspace_id)
            deployments = [deployment_summary_from_cr(cr) for cr in crs]
        except Exception as ex:
            logger.debug("list FlinkDeployments failed", exc_info=True)
            k8s_error = str(ex)
    else:
        k8s_error = "Backend 无法访问 Kubernetes API（需集群内 ServiceAccount 或 kubeconfig）"

    running = sum(1 for d in deployments if d.get("health") == "healthy")
    failed = sum(1 for d in deployments if d.get("health") == "failed")
    suspended = sum(1 for d in deployments if d.get("health") == "suspended")
    starting = sum(1 for d in deployments if d.get("health") == "starting")

    return {
        "runtime": runtime,
        "operator_ready": ready,
        "operator_ready_reason": None if ready else ready_reason,
        "namespace": ns,
        "summary": {
            "deployments_total": len(deployments),
            "running": running,
            "failed": failed,
            "suspended": suspended,
            "starting": starting,
        },
        "deployments": deployments,
        "k8s_error": k8s_error,
    }


def suspend_flink_deployment(
    deployment_name: str,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    api = _custom_objects_api(runtime_ctx)
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    patch = {"spec": {"job": {"state": "suspended"}}}
    return api.patch_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        name=deployment_name,
        body=patch,
    )


def operator_checkpointing_configured(runtime_ctx: Optional[OperatorRuntimeContext] = None) -> bool:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    return bool((ctx.checkpoint_dir or settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip())


def extract_savepoint_info_from_cr(cr: Dict[str, Any]) -> Dict[str, Any]:
    """解析 FlinkDeployment.status.jobStatus.savepointInfo（Operator CR）。"""
    status = cr.get("status") or {}
    job_status = status.get("jobStatus") or {}
    sp_info = job_status.get("savepointInfo") or {}
    last = sp_info.get("lastSavepoint") or {}
    loc = (last.get("location") or job_status.get("upgradeSavepointPath") or "").strip() or None
    ts_raw = last.get("timeStamp") if last.get("timeStamp") is not None else last.get("timestamp")
    ts_ms: Optional[int] = None
    if ts_raw is not None:
        try:
            ts_ms = int(ts_raw)
        except (TypeError, ValueError):
            ts_ms = None
    trigger_type = last.get("triggerType")
    trigger_id = sp_info.get("triggerId")
    trigger_ts_raw = sp_info.get("triggerTimestamp")
    trigger_ts_ms: Optional[int] = None
    if trigger_ts_raw is not None:
        try:
            trigger_ts_ms = int(trigger_ts_raw)
        except (TypeError, ValueError):
            trigger_ts_ms = None
    return {
        "location": loc,
        "timestamp_ms": ts_ms,
        "trigger_type": str(trigger_type).strip() if trigger_type else None,
        "pending": bool(trigger_id),
        "trigger_id": str(trigger_id).strip() if trigger_id else None,
        "trigger_timestamp_ms": trigger_ts_ms,
    }


def savepoint_info_public(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not info or not (info.get("location") or info.get("pending")):
        return None
    return {
        "location": info.get("location"),
        "timestamp_ms": info.get("timestamp_ms"),
        "trigger_type": info.get("trigger_type"),
        "pending": bool(info.get("pending")),
    }


def _savepoint_advanced(baseline: Dict[str, Any], current: Dict[str, Any]) -> bool:
    cur_loc = (current.get("location") or "").strip()
    if not cur_loc:
        return False
    base_loc = (baseline.get("location") or "").strip()
    if not base_loc:
        return True
    if cur_loc != base_loc:
        return True
    base_ts = baseline.get("timestamp_ms") or 0
    cur_ts = current.get("timestamp_ms") or 0
    return cur_ts > base_ts


def _cr_status_error(cr: Dict[str, Any]) -> Optional[str]:
    status = cr.get("status") or {}
    err = status.get("error")
    if err:
        return str(err).strip()
    job_err = (status.get("jobStatus") or {}).get("error")
    if job_err:
        return str(job_err).strip()
    return None


def _next_savepoint_trigger_nonce(cr: Dict[str, Any]) -> int:
    job_spec = (cr.get("spec") or {}).get("job") or {}
    current = job_spec.get("savepointTriggerNonce")
    if current is None:
        return 1
    try:
        return int(current) + 1
    except (TypeError, ValueError):
        return 1


def _patch_flink_deployment_job_spec(
    deployment_name: str,
    job_patch: Dict[str, Any],
    *,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    api = _custom_objects_api(runtime_ctx)
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    body = {"spec": {"job": job_patch}}
    return api.patch_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        name=deployment_name,
        body=body,
    )


def _wait_for_savepoint_after_baseline(
    deployment_name: str,
    baseline: Dict[str, Any],
    *,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    deadline_seconds: float = 180.0,
) -> Dict[str, Any]:
    deadline = time.monotonic() + max(10.0, deadline_seconds)
    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        cr = read_flink_deployment(deployment_name, namespace, runtime_ctx=runtime_ctx)
        sp = extract_savepoint_info_from_cr(cr)
        err = _cr_status_error(cr)
        if err:
            low = err.lower()
            if "savepoint" in low or "checkpoint" in low:
                raise RuntimeError(f"Savepoint 失败: {err}")
            last_err = err
        if _savepoint_advanced(baseline, sp) and sp.get("location") and not sp.get("pending"):
            return sp
        if _savepoint_advanced(baseline, sp) and sp.get("location"):
            return sp
        time.sleep(2.0)
    hint = f"最近错误: {last_err}" if last_err else "请检查 FlinkDeployment 事件与 savepoint 目录权限"
    raise RuntimeError(f"等待 savepoint 完成超时（{int(deadline_seconds)}s）。{hint}")


def _wait_for_suspend_with_savepoint(
    deployment_name: str,
    baseline: Dict[str, Any],
    *,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    deadline_seconds: float = 180.0,
) -> Dict[str, Any]:
    deadline = time.monotonic() + max(10.0, deadline_seconds)
    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        cr = read_flink_deployment(deployment_name, namespace, runtime_ctx=runtime_ctx)
        spec_state = (((cr.get("spec") or {}).get("job") or {}).get("state") or "").strip().lower()
        sp = extract_savepoint_info_from_cr(cr)
        err = _cr_status_error(cr)
        if err:
            low = err.lower()
            if "savepoint" in low or "checkpoint" in low:
                raise RuntimeError(f"停止时 savepoint 失败: {err}")
            last_err = err
        if spec_state == "suspended" and sp.get("location") and _savepoint_advanced(baseline, sp):
            return sp
        if spec_state == "suspended" and sp.get("location") and not (baseline.get("location") or "").strip():
            return sp
        time.sleep(2.0)
    hint = f"最近错误: {last_err}" if last_err else "请确认 upgradeMode=savepoint 且作业处于 RUNNING"
    raise RuntimeError(f"停止超时：未在限定时间内 suspended 且完成 savepoint。{hint}")


def _wait_for_spec_suspended(
    deployment_name: str,
    *,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    deadline_seconds: float = 120.0,
) -> None:
    deadline = time.monotonic() + max(10.0, deadline_seconds)
    while time.monotonic() < deadline:
        cr = read_flink_deployment(deployment_name, namespace, runtime_ctx=runtime_ctx)
        spec_state = (((cr.get("spec") or {}).get("job") or {}).get("state") or "").strip().lower()
        if spec_state == "suspended":
            return
        time.sleep(2.0)
    raise RuntimeError(f"等待 FlinkDeployment suspended 超时（{int(deadline_seconds)}s）")


def suspend_flink_deployment_with_savepoint_guard(
    deployment_name: str,
    *,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    require_savepoint: bool = True,
    deadline_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """
    暂停 FlinkDeployment；require_savepoint 时须 savepoint 成功后才视为停止成功，否则抛错（平台不标 cancelled）。
    upgradeMode=savepoint：Operator suspend 时自动打 savepoint；
    其它模式：先 savepointTriggerNonce 触发 savepoint，再 suspend。
    """
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    timeout = float(
        deadline_seconds
        if deadline_seconds is not None
        else getattr(settings, "FLINK_OPERATOR_STOP_SAVEPOINT_TIMEOUT_SECONDS", 300.0)
    )
    cr = read_flink_deployment(deployment_name, ns, runtime_ctx=ctx)
    spec_job = (cr.get("spec") or {}).get("job") or {}
    upgrade_mode = (spec_job.get("upgradeMode") or "stateless").strip().lower()
    spec_state = (spec_job.get("state") or "").strip().lower()
    baseline_sp = extract_savepoint_info_from_cr(cr)

    if spec_state == "suspended":
        sp = savepoint_info_public(baseline_sp) or baseline_sp
        return {"message": "FlinkDeployment 已处于 suspended", "savepoint": sp}

    if require_savepoint and not operator_checkpointing_configured(ctx):
        raise RuntimeError(
            "停止前要求 savepoint 成功，但未配置 checkpoint/savepoint 目录。"
            "请在 Operator 集群 Profile 或环境变量 FLINK_OPERATOR_CHECKPOINT_DIR 中配置后重试，"
            "或将 require_savepoint=false（不推荐生产有状态作业）。"
        )

    pre_trigger_sp = baseline_sp
    half = max(30.0, timeout * 0.45)

    if require_savepoint and upgrade_mode != "savepoint":
        nonce = _next_savepoint_trigger_nonce(cr)
        _patch_flink_deployment_job_spec(
            deployment_name, {"savepointTriggerNonce": nonce}, namespace=ns, runtime_ctx=ctx
        )
        pre_trigger_sp = _wait_for_savepoint_after_baseline(
            deployment_name, baseline_sp, namespace=ns, runtime_ctx=ctx, deadline_seconds=half
        )

    suspend_flink_deployment(deployment_name, namespace=ns, runtime_ctx=ctx)

    if require_savepoint and upgrade_mode == "savepoint":
        final_sp = _wait_for_suspend_with_savepoint(
            deployment_name, baseline_sp, namespace=ns, runtime_ctx=ctx, deadline_seconds=timeout - half
        )
    elif require_savepoint:
        _wait_for_spec_suspended(deployment_name, namespace=ns, runtime_ctx=ctx, deadline_seconds=half)
        final_sp = pre_trigger_sp
    else:
        _wait_for_spec_suspended(deployment_name, namespace=ns, runtime_ctx=ctx, deadline_seconds=half)
        final_sp = extract_savepoint_info_from_cr(read_flink_deployment(deployment_name, ns, runtime_ctx=ctx))

    pub = savepoint_info_public(final_sp) or final_sp
    loc = pub.get("location") or final_sp.get("location")
    return {
        "message": "已通过 Flink Operator 暂停作业（savepoint 已完成）" if require_savepoint else "已通过 Flink Operator 暂停作业",
        "savepoint": pub,
        "savepoint_location": loc,
    }


def delete_flink_deployment(
    deployment_name: str,
    namespace: Optional[str] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> None:
    """删除 FlinkDeployment CR；Operator 会回收 JM/TM Pod 与 REST Service。CR 已不存在时忽略 404。"""
    api = _custom_objects_api(runtime_ctx)
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    try:
        api.delete_namespaced_custom_object(
            group=FLINK_DEPLOYMENT_GROUP,
            version=FLINK_DEPLOYMENT_VERSION,
            namespace=ns,
            plural=FLINK_DEPLOYMENT_PLURAL,
            name=deployment_name,
        )
        logger.info("已删除 FlinkDeployment %s/%s", ns, deployment_name)
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(e, ApiException) and getattr(e, "status", None) == 404:
            logger.debug("FlinkDeployment 已不存在 %s/%s", ns, deployment_name)
            return
        raise


def extract_status_from_cr(cr: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """返回 (flink_job_id, lifecycle_state, error_message)。"""
    status = cr.get("status") or {}
    job_status = status.get("jobStatus") or {}
    jid = job_status.get("jobId") or job_status.get("jobID")
    jid = str(jid).strip() if jid else None
    lifecycle = status.get("lifecycleState") or status.get("state")
    lifecycle = str(lifecycle).strip() if lifecycle else None
    err = status.get("error") or job_status.get("error")
    err = str(err).strip() if err else None
    return jid, lifecycle, err


def _format_operator_template(tpl: str, deployment_name: str, namespace: str) -> str:
    return (
        tpl.format(
            deployment_name=deployment_name,
            cluster_id=deployment_name,
            namespace=namespace,
        )
        .strip()
        .rstrip("/")
    )


def _jm_rest_template(runtime_ctx: Optional[OperatorRuntimeContext] = None) -> str:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    return (
        ctx.jm_rest_template
        or settings.FLINK_OPERATOR_JM_REST_TEMPLATE
        or settings.FLINK_K8S_APPLICATION_JM_REST_TEMPLATE
        or ""
    ).strip()


def _is_in_cluster_jm_template(tpl: str) -> bool:
    return ".svc.cluster.local" in tpl or ".svc." in tpl


def _resolve_jm_rest_via_k8s_expose(
    deployment_name: str,
    namespace: str,
    *,
    deadline_seconds: float,
) -> Optional[str]:
    kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    if not kc:
        return None

    from app.services.flink_k8s_jm import (
        resolve_application_jm_rest_via_loadbalancer,
        resolve_application_jm_rest_via_nodeport,
    )

    rest_type = (settings.FLINK_K8S_REST_EXPOSED_TYPE or "LoadBalancer").strip().lower()
    ctx = (settings.FLINK_K8S_CONTEXT or "").strip() or None
    if rest_type == "loadbalancer":
        lb = resolve_application_jm_rest_via_loadbalancer(
            cluster_id=deployment_name,
            namespace=namespace,
            kubeconfig_path=kc,
            context=ctx,
            deadline_seconds=deadline_seconds,
        )
        if lb:
            return lb
    if rest_type in ("nodeport", "loadbalancer"):
        np_host = (settings.FLINK_K8S_JM_NODEPORT_HOST or "").strip()
        if not np_host:
            return None
        return resolve_application_jm_rest_via_nodeport(
            cluster_id=deployment_name,
            namespace=namespace,
            kubeconfig_path=kc,
            context=ctx,
            nodeport_host=np_host,
            deadline_seconds=deadline_seconds,
        )
    return None


def resolve_operator_jm_rest(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    job_id: Optional[int] = None,
    deadline_seconds: float = 25.0,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Optional[str]:
    """
    解析 Operator JM REST（后端 API 用）。
    生产（集群内 Backend）：JM_REST 模板（*.svc.cluster.local）→ LB → NodePort。
    本机 Kind 覆盖（DEV_LOCAL=true）：自动隧道 → NodePort → LB。
    """
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    dev_local = bool(getattr(settings, "FLINK_OPERATOR_DEV_LOCAL", False))

    if not dev_local:
        tpl = _jm_rest_template(ctx)
        if tpl:
            return _format_operator_template(tpl, deployment_name, ns)
        return _resolve_jm_rest_via_k8s_expose(
            deployment_name, ns, deadline_seconds=deadline_seconds
        )

    if job_id is not None:
        try:
            from app.services.flink_operator_ui_tunnel import auto_ui_tunnel_enabled, jm_rest_base_via_tunnel

            if auto_ui_tunnel_enabled():
                tunnel_base = jm_rest_base_via_tunnel(int(job_id), deployment_name, ns)
                if tunnel_base:
                    return tunnel_base.rstrip("/")
        except Exception as ex:
            logger.debug("DEV_LOCAL JM REST 隧道解析失败 job=%s: %s", job_id, ex)
    exposed = _resolve_jm_rest_via_k8s_expose(
        deployment_name, ns, deadline_seconds=deadline_seconds
    )
    if exposed:
        return exposed
    tpl = _jm_rest_template(ctx)
    if tpl and not _is_in_cluster_jm_template(tpl):
        return _format_operator_template(tpl, deployment_name, ns)
    return None


def effective_operator_jm_rest(
    job_id: int,
    deployment_name: str,
    namespace: Optional[str] = None,
    stored: Optional[str] = None,
    *,
    deadline_seconds: float = 12.0,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Optional[str]:
    """运行时解析 JM REST；忽略 DB 中不可达的集群内 DNS（本机 Docker Backend）。"""
    resolved = resolve_operator_jm_rest(
        deployment_name,
        namespace,
        job_id=job_id,
        deadline_seconds=deadline_seconds,
        runtime_ctx=runtime_ctx,
    )
    if resolved:
        return resolved
    kept = (stored or "").strip().rstrip("/")
    if kept and not (
        bool(getattr(settings, "FLINK_OPERATOR_DEV_LOCAL", False)) and _is_in_cluster_jm_template(kept)
    ):
        return kept
    return None


def browser_jm_base_for_deployment(
    deployment_name: str,
    namespace: Optional[str] = None,
    jm_rest_internal: Optional[str] = None,
    job_id: Optional[int] = None,
) -> Optional[str]:
    """
    浏览器 Flink Web UI 基址（生产优先）。
    Ingress 模板 → LoadBalancer → NodePort → BROWSER_JM_BASE；
    仅 DEV_LOCAL + AUTO_UI_TUNNEL 时再用 port-forward 隧道。
    """
    ns = namespace or _operator_namespace()

    ui_tpl = (settings.FLINK_OPERATOR_UI_URL_TEMPLATE or "").strip()
    if ui_tpl:
        return _format_operator_template(ui_tpl, deployment_name, ns)

    if job_id is not None:
        from app.services.flink_operator_ui_proxy import (
            operator_ui_proxy_browser_base,
            operator_ui_proxy_enabled,
        )

        if operator_ui_proxy_enabled():
            return operator_ui_proxy_browser_base(int(job_id))

    browser_base = (settings.FLINK_OPERATOR_BROWSER_JM_BASE or "").strip().rstrip("/")
    if browser_base:
        return browser_base

    kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    can_k8s = kubernetes_api_available()
    if can_k8s:
        from app.services.flink_k8s_jm import (
            resolve_application_jm_rest_via_loadbalancer,
            resolve_application_jm_rest_via_nodeport,
        )

        ctx = (settings.FLINK_K8S_CONTEXT or "").strip() or None
        rest_type = (settings.FLINK_K8S_REST_EXPOSED_TYPE or "LoadBalancer").strip().lower()
        kc_path = kc if kc and os.path.isfile(kc) else None
        if rest_type == "loadbalancer":
            lb = resolve_application_jm_rest_via_loadbalancer(
                cluster_id=deployment_name,
                namespace=ns,
                kubeconfig_path=kc_path,
                context=ctx,
                deadline_seconds=12.0,
            )
            if lb:
                return lb
        browser_host = (settings.FLINK_K8S_JM_NODEPORT_BROWSER_HOST or "").strip()
        np_host = (settings.FLINK_K8S_JM_NODEPORT_HOST or browser_host or "").strip()
        if np_host and rest_type in ("nodeport", "loadbalancer"):
            expose_host = browser_host or np_host
            np = resolve_application_jm_rest_via_nodeport(
                cluster_id=deployment_name,
                namespace=ns,
                kubeconfig_path=kc_path,
                context=ctx,
                nodeport_host=expose_host if rest_type == "loadbalancer" else np_host,
                deadline_seconds=12.0,
            )
            if np and browser_host and rest_type == "nodeport" and browser_host != np_host:
                from urllib.parse import urlparse

                try:
                    p = urlparse(np)
                    if p.port:
                        return f"http://{browser_host}:{p.port}".rstrip("/")
                except Exception:
                    pass
            if np:
                return np

    if job_id is not None:
        from app.services.flink_operator_ui_tunnel import auto_ui_tunnel_enabled, browser_base_via_auto_tunnel

        if auto_ui_tunnel_enabled():
            tunnel_base = browser_base_via_auto_tunnel(int(job_id), deployment_name, ns)
            if tunnel_base:
                return tunnel_base

    mapped = jm_rest_url_for_browser(jm_rest_internal)
    if mapped and _is_in_cluster_jm_template(mapped):
        return None
    return mapped


def jm_rest_url_for_browser(jm_rest: Optional[str]) -> Optional[str]:
    """将后端 NodePort 基址（如 host.docker.internal）映射为浏览器 Host（仅开发/NodePort 场景）。"""
    base = (jm_rest or "").strip().rstrip("/")
    if not base:
        return None
    if _is_in_cluster_jm_template(base):
        return None
    backend_host = (settings.FLINK_K8S_JM_NODEPORT_HOST or "host.docker.internal").strip()
    browser_host = (settings.FLINK_K8S_JM_NODEPORT_BROWSER_HOST or "").strip()
    if backend_host and browser_host and f"://{backend_host}" in base:
        return base.replace(f"://{backend_host}", f"://{browser_host}", 1)
    return base


def _browser_jm_needs_port_forward_hint(browser_jm_url: Optional[str]) -> bool:
    """集群内 DNS、本机 127.0.0.1 基址等场景，浏览器须先 kubectl port-forward。"""
    url = (browser_jm_url or "").strip().rstrip("/")
    if not url or _is_in_cluster_jm_template(url):
        return True
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        if host in ("127.0.0.1", "localhost"):
            return True
    except Exception:
        pass
    return False


def operator_ui_port_forward_hint(
    deployment_name: str,
    namespace: Optional[str] = None,
    browser_jm_url: Optional[str] = None,
) -> Optional[str]:
    """浏览器无法直达集群内 DNS 时，提示手工 kubectl port-forward。"""
    from app.services.flink_operator_ui_tunnel import auto_ui_tunnel_enabled

    from app.services.flink_operator_ui_proxy import operator_ui_proxy_enabled

    if operator_ui_proxy_enabled() or auto_ui_tunnel_enabled():
        return None
    if not _browser_jm_needs_port_forward_hint(browser_jm_url):
        return None
    if not getattr(settings, "FLINK_OPERATOR_DEV_LOCAL", False) and not (
        (settings.FLINK_OPERATOR_BROWSER_JM_BASE or "").strip()
        or (settings.FLINK_K8S_JM_NODEPORT_BROWSER_HOST or "").strip()
    ):
        return None
    if browser_jm_url:
        try:
            from urllib.parse import urlparse

            p = urlparse(browser_jm_url.strip())
            fixed = (settings.FLINK_OPERATOR_BROWSER_JM_BASE or "").strip().rstrip("/")
            if fixed and browser_jm_url.strip().rstrip("/") != fixed and p.port and int(p.port) != 8081:
                return None
        except Exception:
            pass
    ns = namespace or _operator_namespace()
    ctx = (settings.FLINK_K8S_CONTEXT or "").strip()
    ctx_flag = f" --context {ctx}" if ctx else ""
    local_port = "8081"
    base = (settings.FLINK_OPERATOR_BROWSER_JM_BASE or "").strip()
    if base:
        try:
            from urllib.parse import urlparse

            p = urlparse(base)
            if p.port:
                local_port = str(p.port)
        except Exception:
            pass
    return (
        f"kubectl{ctx_flag} port-forward -n {ns} svc/{deployment_name}-rest {local_port}:8081\n"
        f"# 须指向本作业的 K8s Service（{deployment_name}-rest），勿与 Session flink-jobmanager 的 8081 混用"
    )


def operator_jm_k8s_service_name(deployment_name: str, namespace: Optional[str] = None) -> str:
    ns = namespace or _operator_namespace()
    return f"{deployment_name}-rest.{ns}.svc.cluster.local:8081"


def wait_for_operator_job_id(
    deployment_name: str,
    namespace: Optional[str] = None,
    deadline_seconds: float = 45.0,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """轮询 FlinkDeployment.status 直至出现 jobId 或超时。"""
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    ns = namespace or ctx.namespace
    deadline = time.monotonic() + deadline_seconds
    last_lifecycle: Optional[str] = None
    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            cr = read_flink_deployment(deployment_name, ns, runtime_ctx=ctx)
            jid, lifecycle, err = extract_status_from_cr(cr)
            last_lifecycle = lifecycle or last_lifecycle
            last_err = err or last_err
            if jid:
                return jid, lifecycle, err
            if lifecycle and lifecycle.upper() in ("FAILED", "FAILING"):
                return None, lifecycle, err or f"FlinkDeployment 进入 {lifecycle}"
        except Exception as ex:
            logger.debug("轮询 FlinkDeployment 状态异常: %s", ex)
        time.sleep(2.0)
    return None, last_lifecycle, last_err or "等待 Operator 回填 jobId 超时"


def submit_jar_via_operator(
    *,
    job_id: int,
    workspace_id: int,
    entry_class: str,
    parallelism: int,
    program_args: Optional[str] = None,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
    jar_path: Optional[str] = None,
    jar_artifacts: Optional[Any] = None,
    job: Optional[Any] = None,
) -> Dict[str, Any]:
    if not (entry_class or "").strip():
        raise RuntimeError("Flink Operator 提交 JAR 须填写入口类（Main Class）。")

    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    deployment_name = deployment_name_for_job(job_id, workspace_id)
    namespace = ctx.namespace
    if jar_artifacts is None:
        if job is None:
            raise RuntimeError("submit_jar_via_operator 须传入 job 或预解析的 jar_artifacts")
        from app.services.jar_artifact import prepare_jar_for_operator_submit

        jar_artifacts = prepare_jar_for_operator_submit(job, ctx)
    jar_uri = jar_artifacts.jar_uri
    fetch_url = jar_artifacts.staging_fetch_url or (
        jar_artifacts.http_download_uri if jar_artifacts.uses_local_staging else None
    )
    meta = deployment_meta or GidoDeploymentMeta(
        workspace_id=int(workspace_id),
        job_id=int(job_id),
        job_type="jar",
    )
    body = build_flink_deployment_body(
        deployment_name=deployment_name,
        namespace=namespace,
        jar_uri=jar_uri,
        entry_class=entry_class.strip(),
        parallelism=parallelism,
        program_args=program_args,
        operator_resources=operator_resources,
        extra_flink_props=extra_flink_props,
        deployment_meta=meta,
        runtime_ctx=ctx,
        jar_http_fetch_url=fetch_url,
    )
    apply_flink_deployment(body, runtime_ctx=ctx)
    out = _submit_flink_deployment_and_wait(
        job_id=job_id,
        deployment_name=deployment_name,
        namespace=namespace,
        artifact_uri=jar_uri,
        runtime_ctx=ctx,
    )
    out["jar_delivery_mode"] = getattr(jar_artifacts, "delivery_mode", None)
    out["jar_s3_uri"] = getattr(jar_artifacts, "s3_uri", None)
    return out


def submit_sql_via_operator(
    *,
    job_id: int,
    workspace_id: int,
    sql_content: str,
    parallelism: int,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    sql_source: str = "mount",
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    if not (sql_content or "").strip():
        raise RuntimeError("SQL 内容为空")

    from app.services.sql_artifact import (
        SQL_MOUNT_PATH,
        build_sql_http_uri_for_operator,
        ensure_sql_script_configmap,
        save_sql_script,
    )

    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    deployment_name = sql_deployment_name_for_job(job_id, workspace_id)
    namespace = ctx.namespace
    save_sql_script(job_id, sql_content, runtime_ctx=ctx)

    source = effective_sql_source(sql_source, runtime_ctx=ctx)
    cm_name: Optional[str] = None
    script_location = SQL_MOUNT_PATH
    http_artifacts = False
    pod_template: Optional[Dict[str, Any]] = None

    if source in ("http", "https"):
        script_location = build_sql_http_uri_for_operator(job_id)
        http_artifacts = True
    elif source == "s3":
        s3_uri = (extra_flink_props or {}).get("sql_s3_uri") if extra_flink_props else None
        if not s3_uri:
            from app.services.sql_artifact import build_sql_s3_uri_for_operator

            script_location = build_sql_s3_uri_for_operator(job_id, runtime_ctx=ctx) or ""
            if not script_location:
                raise RuntimeError(
                    "sql_source=s3 须配置 Operator 集群或平台 JAR 制品 S3 前缀，"
                    "或 streaming_properties.sql_s3_uri"
                )
        else:
            script_location = str(s3_uri)
    else:
        cm_name = ensure_sql_script_configmap(job_id, workspace_id, sql_content, namespace)
        pod_template = _build_pod_template_for_sql_configmap(cm_name)

    meta = deployment_meta or GidoDeploymentMeta(
        workspace_id=int(workspace_id),
        job_id=int(job_id),
        job_type="sql",
    )
    body = build_flink_deployment_body_for_sql(
        deployment_name=deployment_name,
        namespace=namespace,
        sql_script_path=script_location,
        parallelism=parallelism,
        configmap_name=cm_name or "",
        operator_resources=operator_resources,
        extra_flink_props=extra_flink_props,
        deployment_meta=meta,
        pod_template=pod_template,
        enable_http_artifacts=http_artifacts,
        runtime_ctx=ctx,
    )
    apply_flink_deployment(body, runtime_ctx=ctx)
    return _submit_flink_deployment_and_wait(
        job_id=job_id,
        deployment_name=deployment_name,
        namespace=namespace,
        artifact_uri=script_location,
        runtime_ctx=ctx,
    )


def _submit_flink_deployment_and_wait(
    *,
    job_id: int,
    deployment_name: str,
    namespace: str,
    artifact_uri: str,
    runtime_ctx: Optional[OperatorRuntimeContext] = None,
) -> Dict[str, Any]:
    ctx = runtime_ctx or OperatorRuntimeContext.from_settings()
    from app.services.flink_version import operator_image_flink_version_mismatch_warning

    version_warning = operator_image_flink_version_mismatch_warning(ctx.image, ctx.flink_version)
    flink_job_id, lifecycle, err = wait_for_operator_job_id(
        deployment_name, namespace, runtime_ctx=ctx
    )

    jm_rest: Optional[str] = None
    warning: Optional[str] = version_warning
    jm_rest = resolve_operator_jm_rest(
        deployment_name, namespace, job_id=job_id, runtime_ctx=ctx
    )
    if not jm_rest:
        warning = (
            f"已创建 FlinkDeployment `{deployment_name}`（namespace={namespace}）。"
            "未能解析 JM REST NodePort；请确认 spec 含 kubernetes.rest-service.exposed.type=NodePort，"
            "或配置 FLINK_OPERATOR_JM_REST_TEMPLATE。"
        )

    if not flink_job_id and not warning:
        warning = (
            f"FlinkDeployment 已提交（lifecycle={lifecycle or '未知'}）。"
            "Operator 尚未回填 jobId，请稍后在运维页刷新或查看 Flink Web UI。"
        )
    if err and not flink_job_id:
        warning = f"{warning}\n{err}" if warning else err

    try:
        from app.services.flink_operator_ui_tunnel import ensure_ui_tunnel, auto_ui_tunnel_enabled

        if auto_ui_tunnel_enabled():
            ensure_ui_tunnel(job_id, deployment_name, namespace)
    except Exception as ex:
        logger.debug("提交后建立 UI 隧道（可稍后刷新重试）: %s", ex)

    return {
        "flink_job_id": flink_job_id or "",
        "deployment_name": deployment_name,
        "namespace": namespace,
        "jar_uri": artifact_uri,
        "application_jm_rest": jm_rest,
        "lifecycle_state": lifecycle,
        "warning": warning,
    }


def resolve_live_flink_job_id(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    stored: Optional[str] = None,
    job_id: Optional[int] = None,
) -> Optional[str]:
    """从 JM REST 解析当前可打开的 Flink jobId；DB 中旧 id 在 JM 重启后会 404 导致 UI 空白。"""
    jm = resolve_operator_jm_rest(deployment_name, namespace, job_id=job_id, deadline_seconds=8.0)
    if not jm:
        return (stored or "").strip() or None
    base = jm.rstrip("/")
    kept = (stored or "").strip()
    if kept:
        try:
            import requests

            r = requests.get(f"{base}/jobs/{kept}", timeout=6)
            if r.status_code == 200:
                return kept
        except Exception as ex:
            logger.debug("校验 JM jobId %s 失败: %s", kept, ex)
    try:
        import requests

        r = requests.get(f"{base}/jobs/overview", timeout=6)
        if r.status_code == 200:
            for item in (r.json() or {}).get("jobs") or []:
                jid = (item.get("jid") or "").strip()
                state = (item.get("state") or "").strip().upper()
                if jid and state in ("RUNNING", "CREATED", "INITIALIZING", "RESTARTING", "RECONCILING"):
                    return jid
    except Exception as ex:
        logger.debug("JM /jobs/overview 失败: %s", ex)
    return kept or None


def sync_job_from_flink_deployment(
    job_id: int,
    *,
    deployment_name: Optional[str] = None,
    namespace: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    从已存在的 FlinkDeployment CR 回填 jobId / 运行状态（提交 HTTP 500 但 CR 已创建时的补偿）。
    返回建议写入 DB 的字段；CR 不存在或不可读时返回 None。
    """
    dep = (deployment_name or "").strip()
    if not dep:
        dep = deployment_name_for_job(job_id)
    ns = namespace or _operator_namespace()
    try:
        cr = read_flink_deployment(dep, ns)
    except Exception as ex:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(ex, ApiException) and getattr(ex, "status", None) == 404:
            return None
        logger.debug("sync_job_from_flink_deployment(%s): %s", dep, ex)
        return None
    jid, lifecycle, err = extract_status_from_cr(cr)
    spec_state = (cr.get("spec", {}).get("job", {}).get("state") or "").strip().lower()
    patch: Dict[str, Any] = {"flink_operator_deployment_name": dep}
    if spec_state == "suspended":
        patch["status"] = "cancelled"
        return patch
    if jid:
        patch["flink_job_id"] = jid
    lc = (lifecycle or "").upper()
    if lc in ("STABLE", "DEPLOYED", "CREATED", "RUNNING"):
        patch["status"] = "running"
        patch["last_submit_error"] = None
    elif lc in ("FAILED", "FAILING"):
        patch["status"] = "failed"
        if err:
            patch["last_submit_error"] = err
    elif jid:
        patch["status"] = "running"
    jm = resolve_operator_jm_rest(dep, ns, job_id=job_id, deadline_seconds=8.0)
    if jm:
        patch["flink_application_jm_rest"] = jm
    return patch if len(patch) > 1 else None

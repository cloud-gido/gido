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
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Tuple

from app.core.config import settings
from app.services.jar_artifact import resolve_jar_uri_for_operator
from app.services.artifact_s3 import artifact_s3_enabled
from app.services.gido_deployment_meta import (
    GidoDeploymentMeta,
    jar_deployment_name,
    sql_deployment_name,
)
from app.services.flink_pod_scheduling import (
    merge_pod_templates,
    operator_paimon_warehouse_pod_template,
    operator_runtime_pod_template,
    operator_scheduling_pod_template,
)
from app.services.operator_resources import (
    OperatorResources,
    merge_flink_configuration,
    resolve_operator_resources,
)

logger = logging.getLogger(__name__)

FLINK_DEPLOYMENT_GROUP = "flink.apache.org"
FLINK_DEPLOYMENT_VERSION = "v1beta1"
FLINK_DEPLOYMENT_PLURAL = "flinkdeployments"
FLINK_STATE_SNAPSHOT_PLURAL = "flinkstatesnapshots"

_SQL_SET_PATTERN = re.compile(
    r"SET\s+'([^']+)'\s*=\s*'(.*?)'\s*;",
    re.IGNORECASE | re.DOTALL,
)


def extract_sql_set_flink_configuration(sql_content: str) -> Dict[str, str]:
    """从 SQL SET 提取 fs.*，写入 FlinkDeployment flinkConfiguration（Paimon / S3A 凭证）。"""
    props: Dict[str, str] = {}
    if not (sql_content or "").strip():
        return props
    for match in _SQL_SET_PATTERN.finditer(sql_content):
        key = match.group(1).strip()
        if key.startswith("fs."):
            props[key] = match.group(2)
    return props


def deployment_name_for_job(job_id: int, workspace_id: Optional[int] = None, job_name: Optional[str] = None) -> str:
    ws = int(workspace_id) if workspace_id is not None else 0
    return jar_deployment_name(ws, int(job_id), job_name)


def sql_deployment_name_for_job(job_id: int, workspace_id: Optional[int] = None, job_name: Optional[str] = None) -> str:
    ws = int(workspace_id) if workspace_id is not None else 0
    return sql_deployment_name(ws, int(job_id), job_name)


def deployment_name_for_streaming_job(
    job_id: int, job_type: str, workspace_id: Optional[int] = None, job_name: Optional[str] = None
) -> str:
    if (job_type or "").upper() == "SQL":
        return sql_deployment_name_for_job(job_id, workspace_id, job_name)
    return deployment_name_for_job(job_id, workspace_id, job_name)


def _operator_namespace() -> str:
    ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    return ns or "flink"


def kubernetes_api_available() -> bool:
    """Backend 能否访问 K8s API：集群内 ServiceAccount 或可读 kubeconfig 文件。"""
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    return bool(kc and os.path.isfile(kc))


def operator_submit_ready() -> Tuple[bool, str]:
    """Operator 一键提交前置：K8s API + 命名空间 + JAR 拉取基址 + artifact token。"""
    if not kubernetes_api_available():
        return False, (
            "Flink Operator 需要 Kubernetes 访问能力："
            "生产请将 gido-backend 部署在集群内（ServiceAccount + RBAC）；"
            "本机 Kind 开发请在 .env 启用 kind-local 配置并挂载 kubeconfig。"
        )
    if not _operator_namespace():
        return False, "请配置 FLINK_OPERATOR_NAMESPACE（或 FLINK_K8S_NAMESPACE）。"
    if not (settings.FLINK_OPERATOR_ARTIFACT_TOKEN or "").strip():
        return False, "请配置 FLINK_OPERATOR_ARTIFACT_TOKEN（Operator Pod 拉取 JAR 制品校验）。"
    jar_base = (settings.FLINK_OPERATOR_JAR_HTTP_BASE or "").strip()
    if not jar_base and not (settings.FLINK_OPERATOR_JAR_S3_PREFIX or "").strip():
        return False, (
            "请配置 FLINK_OPERATOR_JAR_HTTP_BASE（集群内如 http://backend.gido.svc.cluster.local:8001）"
            "或 FLINK_OPERATOR_JAR_S3_PREFIX。"
        )
    return True, ""


def _load_k8s_config() -> None:
    from kubernetes import config  # type: ignore

    kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    ctx = (settings.FLINK_K8S_CONTEXT or "").strip() or None
    if kc:
        config.load_kube_config(config_file=kc, context=ctx)
        return
    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config(context=ctx)


def _custom_objects_api():
    from kubernetes import client  # type: ignore

    _load_k8s_config()
    return client.CustomObjectsApi()


def _core_v1_api():
    from kubernetes import client  # type: ignore

    _load_k8s_config()
    return client.CoreV1Api()


def ensure_sql_runtime_secret(
    secret_name: str,
    values: Dict[str, str],
    namespace: str,
) -> str:
    """Create/replace the write-only runtime Secret referenced by SqlRunner env."""
    if not values:
        return ""
    api = _core_v1_api()
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": secret_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "gido",
                "gido.io/secret-purpose": "pipeline-runtime",
            },
        },
        "type": "Opaque",
        "stringData": {str(key): str(value) for key, value in values.items()},
    }
    try:
        api.create_namespaced_secret(namespace=namespace, body=body)
    except Exception as exc:
        from kubernetes.client import ApiException  # type: ignore

        if not isinstance(exc, ApiException) or getattr(exc, "status", None) != 409:
            raise
        existing = api.read_namespaced_secret(secret_name, namespace)
        resource_version = getattr(
            getattr(existing, "metadata", None), "resource_version", None
        )
        if resource_version:
            body["metadata"]["resourceVersion"] = resource_version
        api.replace_namespaced_secret(
            name=secret_name, namespace=namespace, body=body
        )
    return secret_name


def sql_runtime_secret_name(deployment_name: str) -> str:
    return f"{deployment_name[:45].rstrip('-')}-pipeline-secrets"


def delete_sql_runtime_secret(
    deployment_name: str,
    namespace: Optional[str] = None,
) -> bool:
    secret_name = sql_runtime_secret_name(deployment_name)
    ns = namespace or _operator_namespace()
    try:
        _core_v1_api().delete_namespaced_secret(
            name=secret_name,
            namespace=ns,
            propagation_policy="Background",
        )
        return True
    except Exception as exc:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(exc, ApiException) and getattr(exc, "status", None) == 404:
            return False
        raise


def _set_sql_runtime_secret_owner(
    secret_name: str,
    namespace: str,
    deployment: Mapping[str, Any],
) -> None:
    metadata = deployment.get("metadata") or {}
    uid = metadata.get("uid")
    name = metadata.get("name")
    if not uid or not name:
        return
    _core_v1_api().patch_namespaced_secret(
        name=secret_name,
        namespace=namespace,
        body={
            "metadata": {
                "ownerReferences": [
                    {
                        "apiVersion": (
                            f"{FLINK_DEPLOYMENT_GROUP}/{FLINK_DEPLOYMENT_VERSION}"
                        ),
                        "kind": "FlinkDeployment",
                        "name": name,
                        "uid": uid,
                        "controller": False,
                        "blockOwnerDeletion": False,
                    }
                ]
            }
        },
    )


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


def _s3_paths_configured() -> bool:
    from app.services.artifact_s3 import artifact_s3_enabled

    if artifact_s3_enabled():
        return True
    ckpt = (settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip().lower()
    if ckpt.startswith("s3://") or ckpt.startswith("s3a://"):
        return True
    wh = (settings.PAIMON_WAREHOUSE_DEFAULT or "").strip().lower()
    return wh.startswith("s3://") or wh.startswith("s3a://")


def _apply_s3_irsa_flink_conf(flink_conf: Dict[str, str]) -> None:
    """EKS 上读 s3/s3a 制品与 checkpoint 须 IRSA；注入 fs.s3a.aws.credentials.provider。"""
    if not getattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True):
        return
    if not _s3_paths_configured():
        return
    provider = (settings.FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER or "").strip()
    if provider:
        flink_conf["fs.s3a.aws.credentials.provider"] = provider


def _base_flink_conf(*, enable_http_artifacts: bool = False) -> Dict[str, str]:
    flink_conf: Dict[str, str] = {}
    if enable_http_artifacts:
        flink_conf["user.artifacts.raw-http-enabled"] = "true"
    ckpt = (settings.FLINK_OPERATOR_CHECKPOINT_DIR or "").strip()
    if ckpt:
        flink_conf["state.checkpoints.dir"] = ckpt
        flink_conf["execution.checkpointing.interval"] = (
            settings.FLINK_OPERATOR_CHECKPOINT_INTERVAL or "60s"
        )
        sp_dir = _resolve_savepoint_dir(ckpt)
        # Flink 官方认 state.savepoints.dir；Operator 文档也常校验
        # execution.checkpointing.savepoint-dir。两边都写，避免计划停止挂起。
        flink_conf["state.savepoints.dir"] = sp_dir
        flink_conf["execution.checkpointing.savepoint-dir"] = sp_dir
    _apply_s3_irsa_flink_conf(flink_conf)
    rest_ex = (settings.FLINK_K8S_REST_EXPOSED_TYPE or "LoadBalancer").strip()
    if rest_ex:
        flink_conf["kubernetes.rest-service.exposed.type"] = rest_ex
    return flink_conf


def _operator_image() -> str:
    return (
        settings.FLINK_OPERATOR_IMAGE
        or settings.FLINK_K8S_APPLICATION_IMAGE
        or "apache/flink:2.0.1-java11"
    ).strip()


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


def _build_pod_template_for_runtime_secret(secret_name: str) -> Dict[str, Any]:
    if not secret_name:
        return {}
    return {
        "spec": {
            "containers": [
                {
                    "name": "flink-main-container",
                    "envFrom": [{"secretRef": {"name": secret_name}}],
                }
            ]
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
) -> Dict[str, Any]:
    resources = operator_resources or resolve_operator_resources(None)
    image = _operator_image()
    flink_version = (settings.FLINK_OPERATOR_FLINK_VERSION or "v2_0").strip()
    sa = (settings.FLINK_OPERATOR_SERVICE_ACCOUNT or "flink").strip() or "flink"

    pj = str((extra_flink_props or {}).get("pipeline.jars") or "")
    need_http = jar_uri.startswith(("http://", "https://")) or ("http://" in pj or "https://" in pj)
    flink_conf = merge_flink_configuration(
        _base_flink_conf(enable_http_artifacts=need_http),
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
    merged_pod_template = merge_pod_templates(
        operator_runtime_pod_template(),
        operator_paimon_warehouse_pod_template(),
        operator_scheduling_pod_template(),
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
) -> Dict[str, Any]:
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
        _base_flink_conf(enable_http_artifacts=enable_http_artifacts),
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
    *,
    jar_version_id: Optional[int] = None,
    artifact_id: Optional[int] = None,
    version_num: Optional[int] = None,
) -> str:
    return resolve_jar_uri_for_operator(
        job_id,
        jar_version_id=jar_version_id,
        artifact_id=artifact_id,
        version_num=version_num,
    )


def effective_sql_source(sql_source: Optional[str]) -> str:
    """S3 制品前缀已配置时，默认 sql_source=s3（EKS 生产，避免仅依赖 ConfigMap）。"""
    source = (sql_source or "mount").strip().lower()
    if source == "mount" and artifact_s3_enabled():
        return "s3"
    return source


def apply_flink_deployment(body: Dict[str, Any]) -> Dict[str, Any]:
    api = _custom_objects_api()
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


def read_flink_deployment(deployment_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    api = _custom_objects_api()
    ns = namespace or _operator_namespace()
    return api.get_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        name=deployment_name,
        _request_timeout=5,
    )


def list_flink_deployments(
    *,
    namespace: Optional[str] = None,
    workspace_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """列出命名空间内 FlinkDeployment CR；可选按 workspace / job 标签过滤。"""
    api = _custom_objects_api()
    ns = namespace or _operator_namespace()
    selectors: List[str] = []
    if workspace_id is not None:
        selectors.append(f"gido.io/workspace-id={int(workspace_id)}")
    if job_id is not None:
        selectors.append(f"gido.io/job-id={int(job_id)}")
    kwargs: Dict[str, Any] = {"_request_timeout": 8}
    if selectors:
        kwargs["label_selector"] = ",".join(selectors)
    resp = api.list_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        **kwargs,
    )
    items = resp.get("items") if isinstance(resp, dict) else None
    return list(items) if isinstance(items, list) else []


def _operator_namespace_candidates(primary: Optional[str] = None) -> List[str]:
    """停止/查找只使用一个明确命名空间，禁止跨命名空间猜测。"""
    namespace = (primary or "").strip() or _operator_namespace()
    return [namespace]


def flink_deployment_accessible(
    deployment_name: str, namespace: Optional[str] = None
) -> bool:
    """GET 能读到 CR 才视为可删目标；404/403/其它错误均视为不可作为删除目标。"""
    name = (deployment_name or "").strip()
    if not name:
        return False
    try:
        read_flink_deployment(name, namespace=namespace)
        return True
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        status = getattr(e, "status", None) if isinstance(e, ApiException) else None
        if status in (403, 404):
            logger.debug(
                "FlinkDeployment 不可访问 %s/%s status=%s",
                namespace or _operator_namespace(),
                name,
                status,
            )
            return False
        logger.debug(
            "FlinkDeployment 探测失败 %s/%s: %s",
            namespace or _operator_namespace(),
            name,
            e,
            exc_info=True,
        )
        return False


def find_flink_deployment_refs_for_job(
    job_id: int,
    *,
    workspace_id: Optional[int] = None,
    preferred_name: Optional[str] = None,
    namespace: Optional[str] = None,
) -> List[Tuple[str, str]]:
    """返回待删除的 (namespace, name)，仅在显式或配置的 Operator 命名空间。"""
    candidates = _operator_namespace_candidates(namespace)

    refs: List[Tuple[str, str]] = []
    seen = set()

    def _add(ns: str, name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        key = (ns, n)
        if key in seen:
            return
        seen.add(key)
        refs.append(key)

    for ns in candidates:
        try:
            for cr in list_flink_deployments(namespace=ns, workspace_id=workspace_id, job_id=job_id):
                n = ((cr.get("metadata") or {}).get("name") or "").strip()
                cr_ns = ((cr.get("metadata") or {}).get("namespace") or ns).strip() or ns
                if n and flink_deployment_accessible(n, namespace=cr_ns):
                    _add(cr_ns, n)
        except Exception:
            logger.debug("list FlinkDeployment ns=%s job_id=%s failed", ns, job_id, exc_info=True)

    pref = (preferred_name or "").strip()
    if pref and not any(n == pref for _, n in refs):
        for ns in candidates:
            if flink_deployment_accessible(pref, namespace=ns):
                _add(ns, pref)
                break

    return refs


def find_flink_deployment_names_for_job(
    job_id: int,
    *,
    workspace_id: Optional[int] = None,
    preferred_name: Optional[str] = None,
    namespace: Optional[str] = None,
) -> List[str]:
    """兼容旧调用：仅返回 name 列表（可能跨命名空间，优先用 find_flink_deployment_refs_for_job）。"""
    return [name for _, name in find_flink_deployment_refs_for_job(
        job_id,
        workspace_id=workspace_id,
        preferred_name=preferred_name,
        namespace=namespace,
    )]


def delete_flink_deployment(deployment_name: str, namespace: Optional[str] = None) -> bool:
    """删除 FlinkDeployment CR。返回 True=本次发出删除；False=CR 本就不存在(404)。其它错误抛出。

    使用与历史可用路径相同的简单 DELETE（propagation_policy 走 query 参数，勿塞错误 body）。
    SQL 作业附带的 runtime Secret 做 best-effort 清理，失败不回滚已删除的 CR。
    """
    api = _custom_objects_api()
    ns = namespace or _operator_namespace()
    deleted = False
    try:
        api.delete_namespaced_custom_object(
            group=FLINK_DEPLOYMENT_GROUP,
            version=FLINK_DEPLOYMENT_VERSION,
            namespace=ns,
            plural=FLINK_DEPLOYMENT_PLURAL,
            name=deployment_name,
            propagation_policy="Background",
        )
        logger.info("已删除 FlinkDeployment %s/%s", ns, deployment_name)
        deleted = True
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(e, ApiException) and getattr(e, "status", None) == 404:
            logger.debug("FlinkDeployment 已不存在 %s/%s", ns, deployment_name)
            deleted = False
        elif isinstance(e, ApiException) and getattr(e, "status", None) == 403:
            # 无权删该 ns：不吞掉——调用方应只对 accessible 的 ref 调用；
            # 这里改写为更短的运维可读错误，避免把整段 HTTP headers 抛到前端。
            raise PermissionError(
                f"无权限删除 FlinkDeployment {ns}/{deployment_name} "
                f"（ServiceAccount 对该命名空间缺少 delete flinkdeployments）。"
                f"请确认作业所在 ns 与 FLINK_OPERATOR_NAMESPACE，并检查 RBAC。"
            ) from e
        else:
            raise

    if deployment_name.startswith("gido-sql-"):
        try:
            delete_sql_runtime_secret(deployment_name, ns)
        except Exception:
            logger.warning(
                "清理 Pipeline runtime Secret 失败 %s/%s（FlinkDeployment 已处理，忽略）",
                ns,
                deployment_name,
                exc_info=True,
            )
    return deleted

def flink_deployment_deletion_state(
    deployment_name: str, namespace: Optional[str] = None
) -> str:
    """返回 gone | terminating | exists | unknown。"""
    try:
        cr = read_flink_deployment(deployment_name, namespace=namespace)
    except Exception as e:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(e, ApiException) and getattr(e, "status", None) == 404:
            return "gone"
        logger.warning("检查 FlinkDeployment 状态失败 %s: %s", deployment_name, e)
        return "unknown"
    ts = ((cr.get("metadata") or {}).get("deletionTimestamp") or "").strip()
    return "terminating" if ts else "exists"


def wait_flink_deployment_reclaimed(
    deployment_name: str,
    *,
    namespace: Optional[str] = None,
    timeout_seconds: float = 30.0,
) -> str:
    """删除后等待 CR 消失或进入 Terminating。返回 gone | terminating | exists | unknown。"""
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last = flink_deployment_deletion_state(deployment_name, namespace=namespace)
    if last in ("gone", "terminating"):
        return last
    while time.monotonic() < deadline:
        time.sleep(1.5)
        last = flink_deployment_deletion_state(deployment_name, namespace=namespace)
        if last in ("gone", "terminating"):
            return last
    return last


def flink_deployment_still_exists(deployment_name: str, namespace: Optional[str] = None) -> bool:
    return flink_deployment_deletion_state(deployment_name, namespace=namespace) != "gone"


def deployment_summary_from_cr(cr: Dict[str, Any]) -> Dict[str, Any]:
    """FlinkDeployment CR → 运维概览行（含 JM/TM 健康摘要）。"""
    meta = cr.get("metadata") or {}
    status = cr.get("status") or {}
    spec = cr.get("spec") or {}
    labels = meta.get("labels") or {}
    annotations = meta.get("annotations") or {}
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
        "job_name": annotations.get("gido.io/job-name"),
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


def patch_flink_deployment_job_state(
    deployment_name: str,
    state: str,
    *,
    namespace: Optional[str] = None,
    upgrade_mode: Optional[str] = None,
    restart_nonce: Optional[int] = None,
    flink_configuration_patch: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Patch Operator job lifecycle fields without replacing the CR."""
    desired_state = (state or "").strip().lower()
    if desired_state not in ("running", "suspended"):
        raise ValueError("FlinkDeployment job state must be running or suspended")
    job_patch: Dict[str, Any] = {"state": desired_state}
    if upgrade_mode is not None:
        mode = str(upgrade_mode).strip().lower()
        if mode not in ("savepoint", "last-state", "stateless"):
            raise ValueError("unsupported FlinkDeployment upgradeMode")
        job_patch["upgradeMode"] = mode
    if restart_nonce is not None:
        job_patch["restartNonce"] = int(restart_nonce)

    body: Dict[str, Any] = {"spec": {"job": job_patch}}
    if flink_configuration_patch:
        cleaned = {
            str(k): (v if isinstance(v, str) else str(v))
            for k, v in flink_configuration_patch.items()
            if v is not None and str(v).strip()
        }
        if cleaned:
            body["spec"]["flinkConfiguration"] = cleaned

    api = _custom_objects_api()
    ns = namespace or _operator_namespace()
    return api.patch_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        name=deployment_name,
        body=body,
    )


def suspend_flink_deployment(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    upgrade_mode: str = "savepoint",
    savepoint_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Suspend a deployment through an Operator-managed savepoint.

    When ``savepoint_dir`` is set, also merge Flink savepoint directory keys so
    already-running CRs (missing ``state.savepoints.dir``) can still complete a
    planned stop without a full redeploy.
    """
    fc_patch: Optional[Dict[str, str]] = None
    sp = (savepoint_dir or "").strip()
    if sp:
        fc_patch = {
            "state.savepoints.dir": sp,
            "execution.checkpointing.savepoint-dir": sp,
        }
    return patch_flink_deployment_job_state(
        deployment_name,
        "suspended",
        namespace=namespace,
        upgrade_mode=upgrade_mode,
        flink_configuration_patch=fc_patch,
    )


def resume_flink_deployment(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    restart_nonce: Optional[int] = None,
    upgrade_mode: str = "savepoint",
) -> Dict[str, Any]:
    """Resume an existing suspended CR, optionally forcing a restart reconciliation."""
    return patch_flink_deployment_job_state(
        deployment_name,
        "running",
        namespace=namespace,
        upgrade_mode=upgrade_mode,
        restart_nonce=restart_nonce,
    )


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def extract_savepoint_status_from_cr(
    cr: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return normalized ``(status, path, error)`` from Operator savepoint status.

    Prefer legacy ``savepointInfo`` when present; also accept
    ``jobStatus.upgradeSavepointPath`` (Operator 1.10+ / 1.15 upgrade path).
    """
    status = cr.get("status") or {}
    job_status = status.get("jobStatus") or status.get("job_status") or {}
    info = job_status.get("savepointInfo") or job_status.get("savepoint_info") or {}
    if not isinstance(info, dict):
        info = {}
    last = (
        info.get("lastSavepoint")
        or info.get("last_savepoint")
        or info.get("savepoint")
        or {}
    )
    if not isinstance(last, dict):
        last = {}

    path = _first_text(
        last.get("location"),
        last.get("path"),
        last.get("savepointPath"),
        last.get("savepoint_path"),
        info.get("location"),
        info.get("path"),
        info.get("savepointPath"),
        info.get("savepoint_path"),
    )
    raw_state = _first_text(
        info.get("status"),
        info.get("state"),
        info.get("triggerStatus"),
        info.get("trigger_status"),
        last.get("status"),
        last.get("state"),
    )
    savepoint_state = raw_state.upper().replace("-", "_").replace(" ", "_") if raw_state else None
    failure_state = savepoint_state in ("FAILED", "FAILURE", "ERROR")
    error = _first_text(
        info.get("failureCause"),
        info.get("failure_cause"),
        info.get("error"),
        info.get("errorMessage"),
        info.get("message") if failure_state else None,
        last.get("failureCause"),
        last.get("failure_cause"),
        last.get("error"),
        last.get("errorMessage"),
    )
    nonterminal_states = {
        "PENDING",
        "IN_PROGRESS",
        "INPROGRESS",
        "TRIGGERED",
        "RUNNING",
    }
    if error:
        savepoint_state = "FAILED"
    elif path and not savepoint_state:
        savepoint_state = "COMPLETED"
    elif path and savepoint_state not in nonterminal_states | {
        "FAILED",
        "FAILURE",
        "ERROR",
    }:
        savepoint_state = "COMPLETED"
    elif not savepoint_state and _first_text(
        info.get("triggerId"), info.get("trigger_id"), info.get("triggerTimestamp")
    ):
        savepoint_state = "PENDING"

    # Operator 1.15：计划停止/升级 Savepoint 常写在 upgradeSavepointPath，
    # 而 savepointInfo 可能一直为空（改由 FlinkStateSnapshot 跟踪）。
    upgrade_path = _first_text(
        job_status.get("upgradeSavepointPath"),
        job_status.get("upgrade_savepoint_path"),
    )
    if upgrade_path and not path:
        path = upgrade_path
        if not savepoint_state or savepoint_state in nonterminal_states:
            savepoint_state = "COMPLETED"
    elif upgrade_path and path and upgrade_path != path and savepoint_state in (
        None,
        *nonterminal_states,
    ):
        # 优先采用升级路径（本次计划停止）
        path = upgrade_path
        savepoint_state = "COMPLETED"

    return savepoint_state, path, error


def list_flink_state_snapshots(
    *,
    namespace: Optional[str] = None,
    deployment_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List FlinkStateSnapshot CRs; empty list when CRD/RBAC unavailable."""
    api = _custom_objects_api()
    ns = namespace or _operator_namespace()
    try:
        out = api.list_namespaced_custom_object(
            group=FLINK_DEPLOYMENT_GROUP,
            version=FLINK_DEPLOYMENT_VERSION,
            namespace=ns,
            plural=FLINK_STATE_SNAPSHOT_PLURAL,
            _request_timeout=10,
        )
    except Exception as exc:
        logger.info(
            "list FlinkStateSnapshot skipped (ns=%s): %s",
            ns,
            exc,
        )
        return []
    items = out.get("items") if isinstance(out, dict) else None
    if not isinstance(items, list):
        return []
    if not deployment_name:
        return [i for i in items if isinstance(i, dict)]
    wanted = str(deployment_name).strip()
    matched: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ref = ((item.get("spec") or {}).get("jobReference") or {})
        if str(ref.get("name") or "").strip() == wanted:
            matched.append(item)
    return matched


def extract_completed_savepoint_from_snapshots(
    snapshots: List[Dict[str, Any]],
    *,
    previous_path: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(path, error)`` from FlinkStateSnapshot list for savepoint type."""
    best_path: Optional[str] = None
    best_error: Optional[str] = None
    for item in snapshots:
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        if not isinstance(spec, dict) or not isinstance(status, dict):
            continue
        # checkpoint-only resources have spec.checkpoint; savepoints have spec.savepoint
        if spec.get("checkpoint") is not None and spec.get("savepoint") is None:
            continue
        state = _first_text(status.get("state"), status.get("status"))
        state_u = state.upper().replace("-", "_") if state else ""
        path = _first_text(status.get("path"), status.get("savepointPath"))
        err = _first_text(status.get("error"), status.get("errorMessage"), status.get("failureCause"))
        if state_u in ("FAILED", "FAILURE", "ERROR", "ABANDONED") and err:
            best_error = err
            continue
        if state_u == "COMPLETED" and path:
            if previous_path and path == previous_path:
                continue
            best_path = path
            best_error = None
            # keep scanning; last completed wins (list order not guaranteed)
    return best_path, best_error


def extract_savepoint_trigger_from_cr(
    cr: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    """Return the Operator savepoint trigger identity and timestamp when exposed."""
    status = cr.get("status") or {}
    job_status = status.get("jobStatus") or status.get("job_status") or {}
    info = job_status.get("savepointInfo") or job_status.get("savepoint_info") or {}
    if not isinstance(info, dict):
        return None, None
    last = (
        info.get("lastSavepoint")
        or info.get("last_savepoint")
        or info.get("savepoint")
        or {}
    )
    if not isinstance(last, dict):
        last = {}
    trigger_id = _first_text(
        info.get("triggerId"),
        info.get("trigger_id"),
        last.get("triggerId"),
        last.get("trigger_id"),
    )
    trigger_timestamp = _first_text(
        last.get("triggerTimestamp"),
        last.get("trigger_timestamp"),
        info.get("triggerTimestamp"),
        info.get("trigger_timestamp"),
    )
    return trigger_id, trigger_timestamp


def _operator_failure_from_cr(cr: Dict[str, Any]) -> Optional[str]:
    _, lifecycle, error = extract_status_from_cr(cr)
    if error:
        return error
    job_status = (cr.get("status") or {}).get("jobStatus") or {}
    job_state = _first_text(job_status.get("state"), job_status.get("jobState"))
    failed_states = {"FAILED", "FAILING", "ERROR"}
    if lifecycle and lifecycle.upper() in failed_states:
        return f"FlinkDeployment entered {lifecycle}"
    if job_state and job_state.upper() in failed_states:
        return f"Flink job entered {job_state}"
    return None


def wait_for_flink_deployment_suspended(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
) -> Dict[str, Any]:
    """Wait until the Operator has reconciled the requested suspended state."""
    ns = namespace or _operator_namespace()
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        cr = read_flink_deployment(deployment_name, ns)
        failure = _operator_failure_from_cr(cr)
        if failure:
            raise RuntimeError(
                f"FlinkDeployment {ns}/{deployment_name} failed while suspending: {failure}"
            )
        spec_state = _first_text(((cr.get("spec") or {}).get("job") or {}).get("state"))
        status = cr.get("status") or {}
        job_status = status.get("jobStatus") or {}
        job_state = _first_text(job_status.get("state"), job_status.get("jobState"))
        lifecycle = _first_text(status.get("lifecycleState"), status.get("state"))
        reconciled = (
            (job_state or "").upper() in ("SUSPENDED", "FINISHED")
            or (lifecycle or "").upper() == "SUSPENDED"
        )
        if (spec_state or "").lower() == "suspended" and reconciled:
            return cr
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for FlinkDeployment {ns}/{deployment_name} to suspend"
            )
        time.sleep(max(0.0, float(poll_interval_seconds)))


def wait_for_flink_deployment_running(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
) -> Dict[str, Any]:
    """Wait until a running desired state is reconciled to a live Flink job."""
    ns = namespace or _operator_namespace()
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        cr = read_flink_deployment(deployment_name, ns)
        failure = _operator_failure_from_cr(cr)
        if failure:
            raise RuntimeError(
                f"FlinkDeployment {ns}/{deployment_name} failed while resuming: {failure}"
            )
        spec_state = _first_text(
            ((cr.get("spec") or {}).get("job") or {}).get("state")
        )
        status = cr.get("status") or {}
        job_status = status.get("jobStatus") or {}
        job_state = _first_text(
            job_status.get("state"),
            job_status.get("jobState"),
        )
        job_id = _first_text(job_status.get("jobId"), job_status.get("jobID"))
        lifecycle = _first_text(status.get("lifecycleState"), status.get("state"))
        reconciled = (job_state or "").upper() == "RUNNING" or (
            bool(job_id)
            and (lifecycle or "").upper() in ("STABLE", "DEPLOYED", "RUNNING")
        )
        if (spec_state or "").lower() == "running" and reconciled:
            return cr
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for FlinkDeployment {ns}/{deployment_name} to resume"
            )
        time.sleep(max(0.0, float(poll_interval_seconds)))


def wait_for_completed_savepoint(
    deployment_name: str,
    namespace: Optional[str] = None,
    *,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 1.0,
    previous_path: Optional[str] = None,
    previous_trigger_id: Optional[str] = None,
    previous_trigger_timestamp: Optional[str] = None,
) -> str:
    """Wait for a completed savepoint and return its durable path.

    Observes (in order):
    1. ``savepointInfo`` / ``upgradeSavepointPath`` on FlinkDeployment
    2. related ``FlinkStateSnapshot`` CRs (Operator 1.15 default)
    """
    ns = namespace or _operator_namespace()
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        cr = read_flink_deployment(deployment_name, ns)
        operator_failure = _operator_failure_from_cr(cr)
        savepoint_state, path, savepoint_error = extract_savepoint_status_from_cr(cr)
        trigger_id, trigger_timestamp = extract_savepoint_trigger_from_cr(cr)
        if savepoint_error or savepoint_state in ("FAILED", "FAILURE", "ERROR"):
            raise RuntimeError(
                f"Savepoint for FlinkDeployment {ns}/{deployment_name} failed: "
                f"{savepoint_error or savepoint_state}"
            )
        if operator_failure:
            raise RuntimeError(
                f"FlinkDeployment {ns}/{deployment_name} failed while saving: {operator_failure}"
            )
        trigger_changed = (
            (
                previous_trigger_id is not None
                and trigger_id is not None
                and trigger_id != previous_trigger_id
            )
            or (
                previous_trigger_timestamp is not None
                and trigger_timestamp is not None
                and trigger_timestamp != previous_trigger_timestamp
            )
        )
        has_previous_trigger = (
            previous_trigger_id is not None
            or previous_trigger_timestamp is not None
        )
        is_fresh = (
            trigger_changed
            if has_previous_trigger
            else (not previous_path or path != previous_path)
        )
        if savepoint_state == "COMPLETED" and path and is_fresh:
            return path

        snapshots = list_flink_state_snapshots(
            namespace=ns, deployment_name=deployment_name
        )
        snap_path, snap_error = extract_completed_savepoint_from_snapshots(
            snapshots, previous_path=previous_path
        )
        if snap_error and not snap_path:
            raise RuntimeError(
                f"Savepoint for FlinkDeployment {ns}/{deployment_name} failed: {snap_error}"
            )
        if snap_path:
            return snap_path

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for savepoint of FlinkDeployment {ns}/{deployment_name}"
            )
        time.sleep(max(0.0, float(poll_interval_seconds)))


def prepare_flink_deployment_for_savepoint_redeploy(
    body: Dict[str, Any],
    savepoint_path: str,
    *,
    savepoint_redeploy_nonce: Optional[int] = None,
    allow_non_restored_state: bool = False,
) -> Dict[str, Any]:
    """Copy a CR body and configure Operator restoration from a savepoint."""
    path = (savepoint_path or "").strip()
    if not path:
        raise ValueError("savepoint_path is required")
    prepared = deepcopy(body)
    job = prepared.setdefault("spec", {}).setdefault("job", {})
    job["initialSavepointPath"] = path
    job["allowNonRestoredState"] = bool(allow_non_restored_state)
    if savepoint_redeploy_nonce is not None:
        job["savepointRedeployNonce"] = int(savepoint_redeploy_nonce)
    return prepared


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


def _jm_rest_template() -> str:
    return (settings.FLINK_OPERATOR_JM_REST_TEMPLATE or settings.FLINK_K8S_APPLICATION_JM_REST_TEMPLATE or "").strip()


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
) -> Optional[str]:
    """
    解析 Operator JM REST（后端 API 用）。
    生产（集群内 Backend）：JM_REST 模板（*.svc.cluster.local）→ LB → NodePort。
    本机 Kind 覆盖（DEV_LOCAL=true）：自动隧道 → NodePort → LB。
    """
    ns = namespace or _operator_namespace()
    dev_local = bool(getattr(settings, "FLINK_OPERATOR_DEV_LOCAL", False))

    if not dev_local:
        tpl = _jm_rest_template()
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
    tpl = _jm_rest_template()
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
) -> Optional[str]:
    """运行时解析 JM REST；忽略 DB 中不可达的集群内 DNS（本机 Docker Backend）。"""
    resolved = resolve_operator_jm_rest(
        deployment_name,
        namespace,
        job_id=job_id,
        deadline_seconds=deadline_seconds,
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
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """轮询 FlinkDeployment.status 直至出现 jobId 或超时。"""
    ns = namespace or _operator_namespace()
    deadline = time.monotonic() + deadline_seconds
    last_lifecycle: Optional[str] = None
    last_err: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            cr = read_flink_deployment(deployment_name, ns)
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
    job_name: Optional[str] = None,
    entry_class: str,
    parallelism: int,
    program_args: Optional[str] = None,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    jar_version_id: Optional[int] = None,
    jar_artifact_id: Optional[int] = None,
    jar_version_num: Optional[int] = None,
    restore_path: Optional[str] = None,
    savepoint_redeploy_nonce: Optional[int] = None,
    allow_non_restored_state: bool = False,
) -> Dict[str, Any]:
    if not (entry_class or "").strip():
        raise RuntimeError("Flink Operator 提交 JAR 须填写入口类（Main Class）。")

    deployment_name = deployment_name_for_job(job_id, workspace_id, job_name)
    namespace = _operator_namespace()
    jar_uri = resolve_jar_uri_for_job(
        job_id,
        jar_version_id=jar_version_id,
        artifact_id=jar_artifact_id,
        version_num=jar_version_num,
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
    )
    if restore_path:
        body = prepare_flink_deployment_for_savepoint_redeploy(
            body,
            restore_path,
            savepoint_redeploy_nonce=savepoint_redeploy_nonce,
            allow_non_restored_state=allow_non_restored_state,
        )
    apply_flink_deployment(body)
    return _submit_flink_deployment_and_wait(
        job_id=job_id,
        deployment_name=deployment_name,
        namespace=namespace,
        artifact_uri=jar_uri,
    )


def submit_sql_via_operator(
    *,
    job_id: int,
    workspace_id: int,
    job_name: Optional[str] = None,
    sql_content: str,
    parallelism: int,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    sql_source: str = "mount",
    restore_path: Optional[str] = None,
    savepoint_redeploy_nonce: Optional[int] = None,
    allow_non_restored_state: bool = False,
    runtime_secret_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if not (sql_content or "").strip():
        raise RuntimeError("SQL 内容为空")

    from app.services.sql_artifact import (
        SQL_MOUNT_PATH,
        build_sql_http_uri_for_operator,
        ensure_sql_script_configmap,
        save_sql_script,
    )

    deployment_name = sql_deployment_name_for_job(job_id, workspace_id, job_name)
    namespace = _operator_namespace()
    deployment_existed = flink_deployment_accessible(
        deployment_name, namespace=namespace
    )
    secret_name = ""
    if runtime_secret_env:
        secret_name = sql_runtime_secret_name(deployment_name)
        secret_name = ensure_sql_runtime_secret(
            secret_name,
            runtime_secret_env,
            namespace,
        )
    save_sql_script(job_id, sql_content)

    source = effective_sql_source(sql_source)
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

            script_location = build_sql_s3_uri_for_operator(job_id) or ""
            if not script_location:
                raise RuntimeError(
                    "sql_source=s3 须配置 FLINK_OPERATOR_JAR_S3_PREFIX / GIDO_ARTIFACT_S3_PREFIX"
                    " 或 streaming_properties.sql_s3_uri"
                )
        else:
            script_location = str(s3_uri)
    else:
        cm_name = ensure_sql_script_configmap(job_id, workspace_id, sql_content, namespace)
        pod_template = _build_pod_template_for_sql_configmap(cm_name)
    pod_template = merge_pod_templates(
        pod_template,
        _build_pod_template_for_runtime_secret(secret_name),
    )

    meta = deployment_meta or GidoDeploymentMeta(
        workspace_id=int(workspace_id),
        job_id=int(job_id),
        job_type="sql",
    )
    merged_flink_extra: Dict[str, Any] = dict(extra_flink_props or {})
    merged_flink_extra.update(extract_sql_set_flink_configuration(sql_content))
    pj = str(merged_flink_extra.get("pipeline.jars") or "")
    if "http://" in pj or "https://" in pj:
        http_artifacts = True
    body = build_flink_deployment_body_for_sql(
        deployment_name=deployment_name,
        namespace=namespace,
        sql_script_path=script_location,
        parallelism=parallelism,
        configmap_name=cm_name or "",
        operator_resources=operator_resources,
        extra_flink_props=merged_flink_extra or None,
        deployment_meta=meta,
        pod_template=pod_template,
        enable_http_artifacts=http_artifacts,
    )
    if restore_path:
        body = prepare_flink_deployment_for_savepoint_redeploy(
            body,
            restore_path,
            savepoint_redeploy_nonce=savepoint_redeploy_nonce,
            allow_non_restored_state=allow_non_restored_state,
        )
    try:
        applied = apply_flink_deployment(body)
    except Exception:
        if secret_name and not deployment_existed:
            delete_sql_runtime_secret(deployment_name, namespace)
        raise
    if secret_name:
        try:
            _set_sql_runtime_secret_owner(secret_name, namespace, applied)
        except Exception:
            logger.warning(
                "设置 Pipeline Secret ownerReference 失败 %s/%s",
                namespace,
                secret_name,
                exc_info=True,
            )
    return _submit_flink_deployment_and_wait(
        job_id=job_id,
        deployment_name=deployment_name,
        namespace=namespace,
        artifact_uri=script_location,
    )


def _submit_flink_deployment_and_wait(
    *,
    job_id: int,
    deployment_name: str,
    namespace: str,
    artifact_uri: str,
) -> Dict[str, Any]:
    flink_job_id, lifecycle, err = wait_for_operator_job_id(deployment_name, namespace)

    jm_rest: Optional[str] = None
    warning: Optional[str] = None
    jm_rest = resolve_operator_jm_rest(deployment_name, namespace, job_id=job_id)
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
    cr: Optional[Dict[str, Any]] = None,
    resolve_jm: bool = True,
    jm_deadline_seconds: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """
    从已存在的 FlinkDeployment CR 回填 jobId / 运行状态（提交 HTTP 500 但 CR 已创建时的补偿）。
    返回建议写入 DB 的字段；CR 不存在或不可读时返回 None。

    resolve_jm=False：仅根据 CR 回填状态（列表/批量轮询用），避免每作业解析 JM 阻塞数秒。
    """
    dep = (deployment_name or "").strip()
    if not dep:
        dep = deployment_name_for_job(job_id)
    ns = namespace or _operator_namespace()
    if cr is None:
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
    if resolve_jm:
        jm = resolve_operator_jm_rest(dep, ns, job_id=job_id, deadline_seconds=jm_deadline_seconds)
        if jm:
            patch["flink_application_jm_rest"] = jm
    return patch if len(patch) > 1 else None

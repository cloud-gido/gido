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
from app.services.jar_artifact import resolve_jar_uri_for_operator
from app.services.artifact_s3 import artifact_s3_enabled
from app.services.gido_deployment_meta import (
    GidoDeploymentMeta,
    jar_deployment_name,
    sql_deployment_name,
)
from app.services.flink_pod_scheduling import merge_pod_templates, operator_scheduling_pod_template
from app.services.operator_resources import (
    OperatorResources,
    merge_flink_configuration,
    resolve_operator_resources,
)

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
        flink_conf["execution.checkpointing.savepoint-dir"] = _resolve_savepoint_dir(ckpt)
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

    flink_conf = merge_flink_configuration(
        _base_flink_conf(enable_http_artifacts=jar_uri.startswith(("http://", "https://"))),
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
    merged_pod_template = merge_pod_templates(operator_scheduling_pod_template(), pod_template)
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


def resolve_jar_uri_for_job(job_id: int) -> str:
    return resolve_jar_uri_for_operator(job_id)


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
    )


def suspend_flink_deployment(deployment_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
    api = _custom_objects_api()
    ns = namespace or _operator_namespace()
    patch = {"spec": {"job": {"state": "suspended"}}}
    return api.patch_namespaced_custom_object(
        group=FLINK_DEPLOYMENT_GROUP,
        version=FLINK_DEPLOYMENT_VERSION,
        namespace=ns,
        plural=FLINK_DEPLOYMENT_PLURAL,
        name=deployment_name,
        body=patch,
    )


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
    entry_class: str,
    parallelism: int,
    program_args: Optional[str] = None,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
) -> Dict[str, Any]:
    if not (entry_class or "").strip():
        raise RuntimeError("Flink Operator 提交 JAR 须填写入口类（Main Class）。")

    deployment_name = deployment_name_for_job(job_id, workspace_id)
    namespace = _operator_namespace()
    jar_uri = resolve_jar_uri_for_job(job_id)
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
    sql_content: str,
    parallelism: int,
    operator_resources: Optional[OperatorResources] = None,
    extra_flink_props: Optional[Dict[str, Any]] = None,
    deployment_meta: Optional[GidoDeploymentMeta] = None,
    sql_source: str = "mount",
) -> Dict[str, Any]:
    if not (sql_content or "").strip():
        raise RuntimeError("SQL 内容为空")

    from app.services.sql_artifact import (
        SQL_MOUNT_PATH,
        build_sql_http_uri_for_operator,
        ensure_sql_script_configmap,
        save_sql_script,
    )

    deployment_name = sql_deployment_name_for_job(job_id, workspace_id)
    namespace = _operator_namespace()
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
    )
    apply_flink_deployment(body)
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

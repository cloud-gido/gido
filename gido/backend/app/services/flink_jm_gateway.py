# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""按 Operator Profile 在目标 K8s 集群部署 JM Ingress 网关（nginx + Ingress），统一转发各 Flink *-rest Service。"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings
from app.services.flink_operator_submit import kubernetes_api_available
from app.services.operator_runtime import OperatorRuntimeContext, resolve_operator_runtime

logger = logging.getLogger(__name__)

GATEWAY_LABEL = "gido.io/component"
GATEWAY_LABEL_VALUE = "flink-jm-gateway"
MANAGED_BY_LABEL = "gido.io/managed-by"
MANAGED_BY_VALUE = "gido"

_NGINX_CONF_TEMPLATE = """server {{
    listen {listen_port};
    server_name _;

    location ~ ^/jm/(?<ns>[^/]+)/(?<dep>[^/]+)(?<path>/.*)?$ {{
        resolver {dns_resolver} valid=10s ipv6=off;
        set $upstream http://$dep-rest.$ns.svc.{cluster_domain}:8081;
        proxy_pass $upstream$path$is_args$args;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }}

    location = /healthz {{
        return 200 'ok';
        add_header Content-Type text/plain;
    }}
}}
"""

_NGINX_CONF_KEY = "gido-jm-gateway.conf"


def gateway_resource_name(profile_id: int) -> str:
    return f"gido-jm-gw-{int(profile_id)}"


def _gateway_listen_port() -> int:
    p = int(getattr(settings, "FLINK_OPERATOR_JM_GATEWAY_PORT", 8080) or 8080)
    return max(1, min(65535, p))


def _gateway_url_port() -> Optional[int]:
    """Backend 访问 jm_rest_template 时 URL 上的端口；None 表示省略（走 Ingress 80/443）。"""
    raw = getattr(settings, "FLINK_OPERATOR_JM_GATEWAY_URL_PORT", None)
    if raw is not None and str(raw).strip() != "":
        p = max(1, min(65535, int(raw)))
        if p in (80, 443):
            return None
        return p
    listen = _gateway_listen_port()
    if listen in (80, 443):
        return None
    return listen


def _host_without_port(host: str) -> str:
    h = (host or "").strip().rstrip("/")
    h = h.replace("https://", "").replace("http://", "").split("/")[0].strip()
    if h.startswith("[") and "]" in h:
        return h.split("]")[0] + "]"
    if ":" in h:
        return h.rsplit(":", 1)[0]
    return h


def jm_rest_template_for_gateway_host(host: str) -> str:
    base = (host or "").strip().rstrip("/")
    if not base:
        raise ValueError("JM 网关 host 不能为空")
    host_part = _host_without_port(base)
    url_port = _gateway_url_port()
    if not base.startswith("http://") and not base.startswith("https://"):
        if url_port is not None:
            base = f"http://{host_part}:{url_port}"
        else:
            base = f"http://{host_part}"
    elif url_port is not None and f":{url_port}" not in base:
        scheme = "https" if base.startswith("https://") else "http"
        base = f"{scheme}://{host_part}:{url_port}"
    return f"{base.rstrip('/')}/jm/{{namespace}}/{{deployment_name}}"


def resolve_gateway_host(profile: Any) -> str:
    explicit = (getattr(profile, "flink_operator_jm_gateway_host", None) or "").strip()
    if explicit:
        return explicit.rstrip("/")
    suffix = (settings.FLINK_OPERATOR_JM_GATEWAY_HOST_SUFFIX or "").strip().lstrip(".")
    if not suffix:
        raise ValueError(
            "启用 JM 网关时须填写 flink_operator_jm_gateway_host，"
            "或配置平台 GIDO_FLINK_OPERATOR_JM_GATEWAY_HOST_SUFFIX（如 flink.internal）"
        )
    slug = re.sub(r"[^a-z0-9-]+", "-", (profile.name or f"p{profile.id}").lower()).strip("-")[:48] or f"p{profile.id}"
    return f"jm-gw-{slug}-p{int(profile.id)}.{suffix}"


def gateway_namespace_for_profile(profile: Any) -> str:
    raw = (getattr(profile, "flink_operator_jm_gateway_namespace", None) or "").strip()
    if raw:
        return raw
    return (settings.FLINK_OPERATOR_JM_GATEWAY_NAMESPACE or "gido-flink-gateway").strip() or "gido-flink-gateway"


def gateway_ingress_class_for_profile(profile: Any) -> str:
    raw = (getattr(profile, "flink_operator_jm_gateway_ingress_class", None) or "").strip()
    if raw:
        return raw
    return (settings.FLINK_OPERATOR_JM_GATEWAY_INGRESS_CLASS or "nginx").strip() or "nginx"


def _k8s_clients(runtime_ctx: OperatorRuntimeContext):
    from kubernetes import client, config  # type: ignore

    configuration = client.Configuration()
    kc = (runtime_ctx.kubeconfig_path or settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    k8s_ctx = (runtime_ctx.k8s_context or settings.FLINK_K8S_CONTEXT or "").strip() or None
    if kc:
        config.load_kube_config(config_file=kc, context=k8s_ctx, client_configuration=configuration)
    else:
        try:
            config.load_incluster_config(client_configuration=configuration)
        except Exception:
            config.load_kube_config(context=k8s_ctx, client_configuration=configuration)
    api_client = client.ApiClient(configuration)
    return (
        client.CoreV1Api(api_client),
        client.AppsV1Api(api_client),
        client.NetworkingV1Api(api_client),
    )


def _gateway_labels(profile_id: int) -> Dict[str, str]:
    return {
        MANAGED_BY_LABEL: MANAGED_BY_VALUE,
        GATEWAY_LABEL: GATEWAY_LABEL_VALUE,
        "gido.io/profile-id": str(int(profile_id)),
    }


_PREFERRED_DNS_SVC_NAMES = (
    "kube-dns",
    "coredns",
    "dns-default",
    "coredns-kube-system",
    "node-local-dns",
)
_DNS_SVC_NAMESPACES = ("kube-system", "openshift-dns")


def _svc_cluster_ip(svc: Any) -> Optional[str]:
    ip = (getattr(getattr(svc, "spec", None), "cluster_ip", None) or "").strip()
    if ip and ip.lower() != "none":
        return ip
    for item in getattr(getattr(svc, "spec", None), "cluster_ips", None) or []:
        s = (item or "").strip()
        if s and s.lower() != "none":
            return s
    return None


def _cluster_dns_resolver_ip(
    core_v1,
    cluster_domain: str,
    *,
    dns_override: Optional[str] = None,
) -> str:
    """nginx 的 resolver 须为 IP；hostname 在启动阶段常解析失败导致 [emerg]。"""
    from kubernetes.client import ApiException  # type: ignore

    override = (dns_override or getattr(settings, "FLINK_OPERATOR_JM_GATEWAY_DNS_IP", None) or "").strip()
    if override:
        return override

    hints: list[str] = []
    for ns in _DNS_SVC_NAMESPACES:
        try:
            svcs = core_v1.list_namespaced_service(ns)
        except ApiException as ex:
            status = getattr(ex, "status", None)
            if status == 404:
                continue
            if status == 403:
                hints.append(f"{ns}/services: 403 Forbidden（kubeconfig 无权 list Service）")
                continue
            raise RuntimeError(f"列举 {ns} Service 失败: {ex}") from ex

        by_name = {svc.metadata.name: svc for svc in svcs.items if svc.metadata and svc.metadata.name}
        for name in _PREFERRED_DNS_SVC_NAMES:
            svc = by_name.get(name)
            if svc:
                ip = _svc_cluster_ip(svc)
                if ip:
                    return ip

        for svc in svcs.items:
            labels = (svc.metadata.labels or {}) if svc.metadata else {}
            k8s_app = (labels.get("k8s-app") or labels.get("k8s_app") or "").lower()
            if k8s_app in ("kube-dns", "coredns", "core-dns"):
                ip = _svc_cluster_ip(svc)
                if ip:
                    return ip

        for svc in svcs.items:
            name = ((svc.metadata.name if svc.metadata else None) or "").lower()
            if "dns" in name and "metrics" not in name and "prom" not in name:
                ip = _svc_cluster_ip(svc)
                if ip:
                    return ip

    domain = (cluster_domain or "cluster.local").strip() or "cluster.local"
    detail = "；".join(hints) if hints else "未发现 kube-dns/coredns Service 或 ClusterIP 为空"
    raise RuntimeError(
        f"无法自动获取集群 DNS Service ClusterIP（domain={domain}）。{detail}。"
        "请在 Operator Profile 填写「集群 DNS IP」，或平台配置 FLINK_OPERATOR_JM_GATEWAY_DNS_IP；"
        "查询命令：kubectl -n kube-system get svc kube-dns coredns -o wide"
    )


def _nginx_configmap_body(
    name: str,
    namespace: str,
    cluster_domain: str,
    dns_resolver: str,
    labels: Dict[str, str],
) -> Dict[str, Any]:
    listen_port = _gateway_listen_port()
    conf = _NGINX_CONF_TEMPLATE.format(
        cluster_domain=cluster_domain,
        dns_resolver=dns_resolver,
        listen_port=listen_port,
    )
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "data": {_NGINX_CONF_KEY: conf},
    }


def _deployment_body(name: str, namespace: str, labels: Dict[str, str]) -> Dict[str, Any]:
    image = (settings.FLINK_OPERATOR_JM_GATEWAY_IMAGE or "nginx:1.27-alpine").strip()
    replicas = max(1, int(getattr(settings, "FLINK_OPERATOR_JM_GATEWAY_REPLICAS", 2) or 2))
    listen_port = _gateway_listen_port()
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {GATEWAY_LABEL: GATEWAY_LABEL_VALUE, "app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": {**labels, "app.kubernetes.io/name": name}},
                "spec": {
                    "containers": [
                        {
                            "name": "nginx",
                            "image": image,
                            "command": ["nginx", "-g", "daemon off;"],
                            "ports": [{"containerPort": listen_port, "name": "http"}],
                            "volumeMounts": [
                                {
                                    "name": "nginx-conf",
                                    "mountPath": f"/etc/nginx/conf.d/{_NGINX_CONF_KEY}",
                                    "subPath": _NGINX_CONF_KEY,
                                    "readOnly": True,
                                }
                            ],
                            "readinessProbe": {
                                "httpGet": {"path": "/healthz", "port": listen_port},
                                "initialDelaySeconds": 2,
                                "periodSeconds": 5,
                            },
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "64Mi"},
                                "limits": {"cpu": "500m", "memory": "256Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {
                            "name": "nginx-conf",
                            "configMap": {"name": name},
                        }
                    ],
                },
            },
        },
    }


def _service_body(name: str, namespace: str, labels: Dict[str, str]) -> Dict[str, Any]:
    listen_port = _gateway_listen_port()
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace, "labels": labels},
        "spec": {
            "selector": {GATEWAY_LABEL: GATEWAY_LABEL_VALUE, "app.kubernetes.io/name": name},
            "ports": [{"name": "http", "port": listen_port, "targetPort": listen_port}],
            "type": "ClusterIP",
        },
    }


def _ingress_body(
    name: str,
    namespace: str,
    host: str,
    ingress_class: str,
    labels: Dict[str, str],
) -> Dict[str, Any]:
    host_clean = _host_without_port(
        host.replace("https://", "").replace("http://", "").split("/")[0].strip()
    )
    listen_port = _gateway_listen_port()
    annotations = {
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "300",
        "nginx.ingress.kubernetes.io/proxy-send-timeout": "300",
        "nginx.ingress.kubernetes.io/proxy-body-size": "50m",
    }
    body: Dict[str, Any] = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": {
            "rules": [
                {
                    "host": host_clean,
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": name,
                                        "port": {"number": listen_port},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }
    if ingress_class:
        body["spec"]["ingressClassName"] = ingress_class
    return body


def _create_or_replace_object(api, create_fn, replace_fn, read_fn, body: Dict[str, Any]) -> Any:
    from kubernetes.client import ApiException  # type: ignore

    meta = body.get("metadata") or {}
    namespace = meta["namespace"]
    name = meta["name"]
    try:
        return create_fn(namespace=namespace, body=body)
    except ApiException as ex:
        if getattr(ex, "status", None) != 409:
            raise
        existing = read_fn(name=name, namespace=namespace)
        em = existing.metadata
        body_meta = body.setdefault("metadata", {})
        if em.resource_version:
            body_meta["resourceVersion"] = em.resource_version
        if em.uid:
            body_meta["uid"] = em.uid
        return replace_fn(name=name, namespace=namespace, body=body)


def _ensure_namespace(core_v1, namespace: str, labels: Dict[str, str]) -> None:
    from kubernetes import client  # type: ignore
    from kubernetes.client import ApiException  # type: ignore

    try:
        core_v1.create_namespace(
            body=client.V1Namespace(
                metadata=client.V1ObjectMeta(name=namespace, labels=labels),
            )
        )
    except ApiException as ex:
        if getattr(ex, "status", None) != 409:
            raise


def _ingress_load_balancer_host(ingress: Any) -> Optional[str]:
    try:
        status = ingress.status
        if not status or not status.load_balancer or not status.load_balancer.ingress:
            return None
        for item in status.load_balancer.ingress:
            if item.hostname:
                return str(item.hostname).strip()
            if item.ip:
                return str(item.ip).strip()
    except Exception:
        return None
    return None


def provision_jm_gateway(
    profile: Any,
    runtime_ctx: OperatorRuntimeContext,
) -> Dict[str, Any]:
    """在 Profile 目标集群创建/更新 JM 网关（Namespace、ConfigMap、Deployment、Service、Ingress）。"""
    if not kubernetes_api_available() and not (runtime_ctx.kubeconfig_path or "").strip():
        raise RuntimeError("Backend 无法访问 Kubernetes API，请配置 kubeconfig 或 in-cluster ServiceAccount")

    profile_id = int(profile.id)
    name = gateway_resource_name(profile_id)
    gw_ns = gateway_namespace_for_profile(profile)
    host = resolve_gateway_host(profile)
    ingress_class = gateway_ingress_class_for_profile(profile)
    cluster_domain = (runtime_ctx.cluster_domain or "cluster.local").strip() or "cluster.local"
    labels = _gateway_labels(profile_id)
    dns_override = (getattr(profile, "flink_operator_jm_gateway_dns_ip", None) or "").strip() or None

    core_v1, apps_v1, net_v1 = _k8s_clients(runtime_ctx)
    _ensure_namespace(core_v1, gw_ns, {MANAGED_BY_LABEL: MANAGED_BY_VALUE})

    dns_resolver = _cluster_dns_resolver_ip(core_v1, cluster_domain, dns_override=dns_override)
    cm_body = _nginx_configmap_body(name, gw_ns, cluster_domain, dns_resolver, labels)
    dep_body = _deployment_body(name, gw_ns, labels)
    svc_body = _service_body(name, gw_ns, labels)
    ing_body = _ingress_body(name, gw_ns, host, ingress_class, labels)

    _create_or_replace_object(
        core_v1,
        core_v1.create_namespaced_config_map,
        core_v1.replace_namespaced_config_map,
        core_v1.read_namespaced_config_map,
        cm_body,
    )
    _create_or_replace_object(
        apps_v1,
        apps_v1.create_namespaced_deployment,
        apps_v1.replace_namespaced_deployment,
        apps_v1.read_namespaced_deployment,
        dep_body,
    )
    _create_or_replace_object(
        core_v1,
        core_v1.create_namespaced_service,
        core_v1.replace_namespaced_service,
        core_v1.read_namespaced_service,
        svc_body,
    )
    ingress = _create_or_replace_object(
        net_v1,
        net_v1.create_namespaced_ingress,
        net_v1.replace_namespaced_ingress,
        net_v1.read_namespaced_ingress,
        ing_body,
    )

    lb_hint = _ingress_load_balancer_host(ingress)
    listen_port = _gateway_listen_port()
    jm_tpl = jm_rest_template_for_gateway_host(host)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ready": True,
        "provisioned_at": now,
        "host": host.replace("https://", "").replace("http://", "").split("/")[0],
        "gateway_namespace": gw_ns,
        "ingress_class": ingress_class,
        "jm_rest_template": jm_tpl,
        "gateway_port": listen_port,
        "gateway_url_port": _gateway_url_port(),
        "ingress_load_balancer": lb_hint,
        "dns_resolver": dns_resolver,
        "resources": {
            "namespace": gw_ns,
            "configmap": name,
            "deployment": name,
            "service": name,
            "ingress": name,
        },
        "message": (
            f"已部署 JM 网关 Ingress（host={host}）。"
            f"请将 DNS 或 hosts 指向 Ingress 入口"
            f"{f'（当前 LB: {lb_hint}）' if lb_hint else ''}；"
            f"GIDO Backend 须能访问该 host。"
        ),
    }


def provision_jm_gateway_for_profile(db, profile: Any) -> Dict[str, Any]:
    ctx = resolve_operator_runtime(db, int(profile.workspace_id), int(profile.id), None)
    return provision_jm_gateway(profile, ctx)

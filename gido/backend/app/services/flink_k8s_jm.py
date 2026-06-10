# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
K8s Application / Operator：从集群读取 JM REST Service 的 NodePort 或 LoadBalancer，
供 GIDO Backend 轮询 jobId、取消作业等。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _load_k8s_v1(kubeconfig_path: Optional[str], context: Optional[str]):
    """kubeconfig 文件优先；否则集群内 ServiceAccount（与 Operator UI 隧道同路径）。"""
    from kubernetes import client, config  # type: ignore

    kc = (kubeconfig_path or "").strip()
    if kc and os.path.isfile(kc):
        config.load_kube_config(config_file=kc, context=context)
        return client.CoreV1Api()
    try:
        config.load_incluster_config()
    except Exception:
        if kc:
            config.load_kube_config(config_file=kc, context=context)
        else:
            config.load_kube_config(context=context)
    return client.CoreV1Api()


def _rest_service_name(cluster_id: str) -> str:
    return f"{(cluster_id or '').strip().lower()}-rest"


def _ingress_host_from_service(svc) -> Tuple[Optional[str], Optional[int]]:
    """从 LoadBalancer Service 取对外 host 与 port（默认 8081）。"""
    port = 8081
    for p in svc.spec.ports or []:
        if getattr(p, "port", None):
            port = int(p.port)
            break
    for ing in (svc.status.load_balancer.ingress or []) if svc.status and svc.status.load_balancer else []:
        host = (getattr(ing, "hostname", None) or getattr(ing, "ip", None) or "").strip()
        if host:
            return host, port
    return None, port


def resolve_application_jm_rest_via_loadbalancer(
    *,
    cluster_id: str,
    namespace: str,
    kubeconfig_path: Optional[str] = None,
    context: Optional[str] = None,
    deadline_seconds: float = 120.0,
    scheme: str = "http",
) -> Optional[str]:
    """轮询 ``{cluster_id}-rest`` LoadBalancer 直到分配 external IP/hostname。"""
    try:
        from kubernetes.client import ApiException  # type: ignore
    except ImportError:
        logger.warning("未安装 kubernetes 包，无法解析 JM LoadBalancer")
        return None

    cid = (cluster_id or "").strip().lower()
    ns = (namespace or "").strip()
    if not cid or not ns:
        return None

    try:
        v1 = _load_k8s_v1(kubeconfig_path, context)
    except Exception as ex:
        logger.warning("load_kube_config 失败: %s", ex)
        return None

    name = _rest_service_name(cid)
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            svc = v1.read_namespaced_service(name=name, namespace=ns)
            host, port = _ingress_host_from_service(svc)
            if host:
                return f"{scheme}://{host}:{port}".rstrip("/")
        except ApiException as ex:
            if ex.status == 403:
                logger.warning("无权限读取 Service %s/%s: %s", ns, name, ex)
                return None
            if ex.status not in (404,):
                logger.debug("read_namespaced_service(%s/%s): %s", ns, name, ex)
        except Exception as ex:
            logger.debug("轮询 JM LoadBalancer 异常: %s", ex)
        time.sleep(2.0)
    return None


def resolve_application_jm_rest_via_nodeport(
    *,
    cluster_id: str,
    namespace: str,
    kubeconfig_path: Optional[str] = None,
    context: Optional[str] = None,
    nodeport_host: str = "",
    deadline_seconds: float = 120.0,
) -> Optional[str]:
    """
    轮询直到 Service ``{cluster_id}-rest`` 存在且分配了 NodePort，返回 ``http://{nodeport_host}:{nodePort}``。
    生产环境 nodeport_host 填节点内网 IP 或 FLINK_K8S_JM_NODEPORT_BROWSER_HOST。
    """
    try:
        from kubernetes.client import ApiException  # type: ignore
    except ImportError:
        logger.warning("未安装 kubernetes 包，无法自动解析 JM NodePort")
        return None

    cid = (cluster_id or "").strip().lower()
    ns = (namespace or "").strip()
    host = (nodeport_host or "").strip()
    if not cid or not ns or not host:
        return None

    try:
        v1 = _load_k8s_v1(kubeconfig_path, context)
    except Exception as ex:
        logger.warning("load_kube_config 失败: %s", ex)
        return None

    name = _rest_service_name(cid)
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            svc = v1.read_namespaced_service(name=name, namespace=ns)
            for p in svc.spec.ports or []:
                np = getattr(p, "node_port", None)
                if np:
                    return f"http://{host}:{int(np)}".rstrip("/")
        except ApiException as ex:
            if ex.status == 403:
                logger.warning("无权限读取 Service %s/%s: %s", ns, name, ex)
                return None
            if ex.status not in (404,):
                logger.debug("read_namespaced_service(%s/%s): %s", ns, name, ex)
        except Exception as ex:
            logger.debug("轮询 JM Service 异常: %s", ex)
        time.sleep(1.8)
    return None

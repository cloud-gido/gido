# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
K8s Application：从集群读取 JM REST Service 的 NodePort，供跑在 Docker 宿主机/bridge 上的后端访问。
（*.svc.cluster.local 在容器外通常不可解析，故用 host.docker.internal:<NodePort> 等。）
"""
from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_application_jm_rest_via_nodeport(
    *,
    cluster_id: str,
    namespace: str,
    kubeconfig_path: str,
    context: Optional[str],
    nodeport_host: str,
    deadline_seconds: float = 120.0,
) -> Optional[str]:
    """
    轮询直到 Service ``{cluster_id}-rest`` 存在且分配了 NodePort，返回 ``http://{nodeport_host}:{nodePort}``。
    """
    try:
        from kubernetes import client, config  # type: ignore
        from kubernetes.client import ApiException  # type: ignore
    except ImportError:
        logger.warning("未安装 kubernetes 包，无法自动解析 JM NodePort")
        return None

    cid = (cluster_id or "").strip().lower()
    ns = (namespace or "").strip()
    host = (nodeport_host or "").strip() or "host.docker.internal"
    if not cid or not ns:
        return None

    try:
        config.load_kube_config(config_file=kubeconfig_path, context=context)
    except Exception as ex:
        logger.warning("load_kube_config 失败: %s", ex)
        return None

    v1 = client.CoreV1Api()
    name = f"{cid}-rest"
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

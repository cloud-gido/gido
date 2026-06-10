# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Operator 作业 Flink Web UI：本机开发自动 port-forward，免手工转发。"""
from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_tunnels: Dict[str, subprocess.Popen] = {}
_pf_threads: Dict[str, threading.Thread] = {}


def _tunnel_key(deployment_name: str, namespace: str) -> str:
    return f"{namespace}/{deployment_name}"


def auto_ui_tunnel_enabled() -> bool:
    explicit = getattr(settings, "FLINK_OPERATOR_AUTO_UI_TUNNEL", None)
    if explicit is not None:
        return bool(explicit)
    return bool(getattr(settings, "FLINK_OPERATOR_DEV_LOCAL", False))


def _port_offset(job_id: int) -> int:
    return int(job_id) % 1000


def browser_port_for_job(job_id: int) -> int:
    """宿主机 / 浏览器访问端口（compose 映射到 bind 端口）。"""
    base = int(getattr(settings, "FLINK_OPERATOR_UI_LOCAL_PORT_BASE", None) or 22000)
    return base + _port_offset(job_id)


def bind_port_for_job(job_id: int) -> int:
    """容器内 kubectl port-forward 实际监听端口。"""
    base = int(getattr(settings, "FLINK_OPERATOR_UI_TUNNEL_BIND_PORT_BASE", None) or 32000)
    return base + _port_offset(job_id)


def local_port_for_job(job_id: int) -> int:
    """兼容旧名：浏览器端口。"""
    return browser_port_for_job(job_id)


def jm_rest_base_via_tunnel(job_id: int, deployment_name: str, namespace: str) -> Optional[str]:
    """Backend 容器内调 JM REST（走 bind 端口，不经 Docker 映射层）。"""
    if not ensure_ui_tunnel(job_id, deployment_name, namespace):
        return None
    return f"http://127.0.0.1:{bind_port_for_job(job_id)}"


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


def _k8s_core_v1():
    from kubernetes import client  # type: ignore

    _load_k8s_config()
    return client.CoreV1Api()


def _svc_exists(deployment_name: str, namespace: str) -> Tuple[bool, str]:
    """用 Python K8s 客户端探测 Service（与 Operator 提交同路径，不依赖 kubectl）。"""
    svc_name = f"{deployment_name}-rest"
    try:
        from kubernetes.client import ApiException  # type: ignore

        svc = _k8s_core_v1().read_namespaced_service(svc_name, namespace)
        ip = (svc.spec.cluster_ip or "").strip() if svc.spec else ""
        if ip and ip != "None":
            return True, ""
        return False, "clusterIP 为空"
    except Exception as ex:
        from kubernetes.client import ApiException  # type: ignore

        if isinstance(ex, ApiException) and getattr(ex, "status", None) == 404:
            return False, "Service 尚未创建"
        return False, str(ex)[:200]


def _wait_svc_ready(deployment_name: str, namespace: str, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    svc_name = f"{deployment_name}-rest"
    last_reason = ""
    while time.monotonic() < deadline:
        ok, reason = _svc_exists(deployment_name, namespace)
        if ok:
            return True
        last_reason = reason
        time.sleep(2.0)
    logger.warning(
        "Service %s/%s 未就绪（%s），UI 隧道暂不建立",
        namespace,
        svc_name,
        last_reason or "超时",
    )
    return False


def _kubectl_base_cmd() -> list:
    cmd = [(getattr(settings, "FLINK_OPERATOR_KUBECTL_PATH", None) or "kubectl").strip() or "kubectl"]
    kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
    ctx = (settings.FLINK_K8S_CONTEXT or "").strip()
    if kc:
        cmd.extend(["--kubeconfig", kc])
    if ctx:
        cmd.extend(["--context", ctx])
    return cmd


def _start_tunnel_subprocess(deployment_name: str, namespace: str, bind_port: int) -> subprocess.Popen:
    cmd = _kubectl_base_cmd() + [
        "port-forward",
        "--address",
        "0.0.0.0",
        "-n",
        namespace,
        f"svc/{deployment_name}-rest",
        f"{bind_port}:8081",
    ]
    logger.info("自动 UI 隧道: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _start_tunnel_k8s_python(deployment_name: str, namespace: str, bind_port: int) -> bool:
    """无 kubectl 时用官方 portforward 库（Pod 8081 → 本地 bind_port）。"""
    from kubernetes.stream import portforward  # type: ignore

    v1 = _k8s_core_v1()
    pods = v1.list_namespaced_pod(
        namespace=namespace,
        label_selector=f"app={deployment_name},component=jobmanager",
    )
    if not pods.items:
        raise RuntimeError(f"未找到 JM Pod app={deployment_name}")
    pod_name = pods.items[0].metadata.name
    key = _tunnel_key(deployment_name, namespace)
    ready = threading.Event()

    def _run() -> None:
        try:
            portforward(
                v1.connect_get_namespaced_pod_portforward,
                pod_name,
                namespace,
                ports="8081",
                _request_timeout=86400,
            )
        except Exception as ex:
            logger.warning("Python port-forward 结束 %s: %s", key, ex)
        finally:
            ready.set()

    th = threading.Thread(target=_run, name=f"flink-ui-pf-{deployment_name}", daemon=True)
    th.start()
    _pf_threads[key] = th
    return True


def _local_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def _tunnel_healthy(bind_port: int) -> bool:
    if not _local_port_open(bind_port):
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{bind_port}/overview", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _kubectl_available() -> bool:
    try:
        proc = subprocess.run(
            _kubectl_base_cmd() + ["version", "--client"],
            capture_output=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def ensure_ui_tunnel(job_id: int, deployment_name: str, namespace: str) -> Optional[int]:
    """为 Operator JM 建立/复用 port-forward，返回浏览器端口（宿主机访问）。"""
    if not auto_ui_tunnel_enabled():
        return None

    browser_port = browser_port_for_job(job_id)
    bind_port = bind_port_for_job(job_id)
    key = _tunnel_key(deployment_name, namespace)

    with _lock:
        if _tunnel_healthy(bind_port):
            return browser_port
        existing = _tunnels.get(key)
        if existing is not None and existing.poll() is None and _tunnel_healthy(bind_port):
            return browser_port
        if existing is not None:
            try:
                existing.terminate()
                existing.wait(timeout=2)
            except Exception:
                try:
                    existing.kill()
                except Exception:
                    pass
            _tunnels.pop(key, None)
        _pf_threads.pop(key, None)

    if not _wait_svc_ready(deployment_name, namespace, timeout=8.0):
        return None

    try:
        with _lock:
            if _tunnel_healthy(bind_port):
                return browser_port

            if _kubectl_available():
                proc = _start_tunnel_subprocess(deployment_name, namespace, bind_port)
                time.sleep(1.2)
                if proc.poll() is None and _tunnel_healthy(bind_port):
                    _tunnels[key] = proc
                    logger.info(
                        "UI 隧道已建立 job=%s → 127.0.0.1:%s (bind %s)",
                        job_id,
                        browser_port,
                        bind_port,
                    )
                    return browser_port
                err = (proc.stderr.read() or b"").decode("utf-8", errors="replace")[:500]
                if proc.poll() is None:
                    proc.terminate()
                logger.warning(
                    "kubectl UI 隧道失败 job=%s bind=%s: %s",
                    job_id,
                    bind_port,
                    err,
                )

            _start_tunnel_k8s_python(deployment_name, namespace, bind_port)
            time.sleep(2.0)
            if _tunnel_healthy(bind_port):
                logger.info(
                    "UI 隧道已建立（Python）job=%s → 127.0.0.1:%s (bind %s)",
                    job_id,
                    browser_port,
                    bind_port,
                )
                return browser_port
            logger.warning("UI 隧道未就绪 job=%s bind=%s", job_id, bind_port)
            return None
    except FileNotFoundError:
        logger.error("kubectl 不可用且 Python port-forward 失败 job=%s", job_id)
        return None
    except Exception as ex:
        logger.warning("UI 隧道启动异常 job=%s: %s", job_id, ex)
        return None


def release_ui_tunnel(deployment_name: str, namespace: str) -> None:
    key = _tunnel_key(deployment_name, namespace)
    with _lock:
        proc = _tunnels.pop(key, None)
        _pf_threads.pop(key, None)
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def release_all_ui_tunnels() -> None:
    with _lock:
        keys = list(set(list(_tunnels.keys()) + list(_pf_threads.keys())))
    for key in keys:
        dep, ns = key.split("/", 1)
        release_ui_tunnel(dep, ns)


def browser_base_via_auto_tunnel(
    job_id: int,
    deployment_name: str,
    namespace: str,
) -> Optional[str]:
    port = ensure_ui_tunnel(job_id, deployment_name, namespace)
    if not port:
        return None
    host = (getattr(settings, "FLINK_OPERATOR_UI_TUNNEL_BROWSER_HOST", None) or "127.0.0.1").strip()
    return f"http://{host}:{port}"

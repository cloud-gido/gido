# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
实时开发 API — 封装 Flink REST API
支持 SQL 任务（通过 Flink SQL Gateway）和 JAR 任务
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Body, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from app.core import perm_codes as PC
from sqlalchemy.orm import Session
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    ForeignKey,
    JSON,
    BigInteger,
    UniqueConstraint,
    Index,
    event,
    inspect as sa_inspect,
    func,
)
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Tuple, Set, Dict, Any
from datetime import datetime
from app.core.database import get_db, Base
from app.core.security import get_current_user
from app.core.config import settings
from app.models.workspace import User, FlinkSessionProfile, NodeFolder
from app.services.flink_runtime import (
    FlinkRuntimeConfig,
    apply_flink_row_overrides,
    get_flink_runtime,
    get_flink_runtime_for_workspace_profile,
)
from app.services.flink_k8s_jm import resolve_application_jm_rest_via_nodeport
from app.services.flink_submit_mode import (
    default_jar_submit_mode as _default_jar_submit_mode,
    default_sql_submit_mode as _default_sql_submit_mode,
    enforce_jar_submit_mode_allowed as _enforce_jar_submit_mode_allowed,
    enforce_sql_submit_mode_allowed as _enforce_sql_submit_mode_allowed,
    normalize_jar_submit_mode as _normalize_jar_submit_mode,
    normalize_sql_submit_mode as _normalize_sql_submit_mode,
)
from app.services.rbac import (
    assert_workspace_data_capability,
    assert_gido_stream_infra_probe_access,
    require_streaming_job,
    workspace_data_full_control,
)
from app.services.publish_approval import assert_can_publish_production
import os
import requests
import logging
from urllib.parse import urlparse, quote
import re
import time
import json
import hashlib

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

STREAM_JOB_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,48}[a-z0-9]$")
STREAM_JOB_NAME_RULE = "作业名须为 3-50 位小写字母、数字、短横线，且以字母开头、以字母或数字结尾，例如 s3-copy-users"


def normalize_stream_job_name(name: str) -> str:
    value = (name or "").strip()
    if not STREAM_JOB_NAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail=STREAM_JOB_NAME_RULE)
    return value


def ensure_stream_job_name_available(
    db: Session,
    workspace_id: int,
    name: str,
    *,
    exclude_job_id: Optional[int] = None,
) -> None:
    q = db.query(StreamingJob).filter(
        StreamingJob.workspace_id == int(workspace_id),
        StreamingJob.name == name,
    )
    if exclude_job_id is not None:
        q = q.filter(StreamingJob.id != int(exclude_job_id))
    if q.first():
        raise HTTPException(status_code=409, detail=f"作业名 {name} 已存在")


def _flink_http_session_for_get() -> requests.Session:
    """对 JM / Gateway 的 GET 做短重试，缓解 JM 重启、Pod 未就绪或 LB 瞬断导致的 RemoteDisconnected。

    connect 次数须克制：JM Connection refused（僵尸 deployment / REST 未起）时，
    过高 connect 重试会把 jobs-status-sync 拖成数十秒并刷爆日志，拖累运维页。
    """
    sess = requests.Session()
    retry = Retry(
        total=2,
        connect=1,
        read=2,
        backoff_factor=0.2,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    return sess


_FLINK_HTTP_GET = _flink_http_session_for_get()

# FlinkClient.apply_runtime：未显式传入 K8s 合并字段时仅从 settings 初始化（模块 import 时）
_FLINK_K8S_RT_UNDEF = object()


def _parse_job_streaming_properties(raw: Optional[str]) -> dict:
    """作业上保存的 JSON 对象字符串 → dict，非法则空（不阻断提交）。"""
    if not raw or not str(raw).strip():
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _split_streaming_properties_for_sql(extra: Optional[dict]) -> Tuple[dict, dict]:
    """
    拆出 (Gateway session properties, K8s executionConfig 覆盖)。
    顶级键 `k8s_application` 若为对象，其键值并入 Flink deploy-script 的 executionConfig（字符串化）。
    """
    if not extra:
        return {}, {}
    raw_ka = extra.get("k8s_application")
    props: dict = {}
    for k, v in extra.items():
        if k == "k8s_application":
            continue
        if v is None:
            continue
        props[str(k)] = v if isinstance(v, str) else str(v)
    exec_overrides: dict = {}
    if isinstance(raw_ka, dict):
        for k, v in raw_ka.items():
            if v is None:
                continue
            exec_overrides[str(k)] = v if isinstance(v, str) else str(v)
    return props, exec_overrides


router = APIRouter(prefix="/streaming", tags=["实时开发"])


def _refresh_flink_from_db(db: Session = Depends(get_db)) -> None:
    """请求进入实时 API 前，用库内 Flink 覆盖项刷新全局客户端。"""
    from app.services.flink_runtime import refresh_flink_client

    refresh_flink_client(db)


def get_db_flink(db: Session = Depends(get_db)) -> Session:
    from app.services.flink_runtime import refresh_flink_client

    refresh_flink_client(db)
    return db


def _flink_runtime_cfg_for_job(db: Session, job: StreamingJob) -> FlinkRuntimeConfig:
    return get_flink_runtime_for_workspace_profile(
        db, int(job.workspace_id), getattr(job, "flink_session_profile_id", None)
    )


def _username_map(db: Session, user_ids: List[Optional[int]]) -> dict:
    """批量解析用户名，避免列表接口 N+1 查询。"""
    clean = {int(i) for i in user_ids if i is not None}
    if not clean:
        return {}
    out: dict = {}
    for u in db.query(User).filter(User.id.in_(list(clean))).all():
        out[u.id] = u.username
    return out


def _compute_flink_operational(job: StreamingJob, *, runtime_cfg: FlinkRuntimeConfig) -> dict:
    """面向运维台的就绪度与提示（与真实 Flink 状态互补，不替代 get_status）。"""
    hints: List[str] = []
    readiness = "neutral"
    if (job.job_type or "").upper() != "SQL":
        return {"readiness": readiness, "hints": hints, "submit_mode": None}
    mode = _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None))
    tmpl = (runtime_cfg.flink_k8s_application_jm_rest_template or "").strip()
    img = (runtime_cfg.flink_k8s_application_image or "").strip()
    cid = (getattr(job, "flink_application_cluster_id", None) or "").strip() or None
    fjid = (job.flink_job_id or "").strip() or None
    jmrest = (getattr(job, "flink_application_jm_rest", None) or "").strip() or None
    op_dep = (getattr(job, "flink_operator_deployment_name", None) or "").strip() or None
    st = (job.status or "").strip().lower()
    if mode == "flink_operator":
        from app.services.flink_operator_submit import sql_operator_submit_ready

        ok, reason = sql_operator_submit_ready()
        if not ok:
            readiness = "blocked"
            hints.append(reason)
        else:
            hints.append(
                "SQL Operator 使用 FlinkDeployment + SQL Runner；FLINK_OPERATOR_IMAGE 须包含 sql-runner.jar。"
            )
            if st == "running" and not fjid and not op_dep:
                readiness = "warning"
                hints.append("平台为 running 但未记录 Operator CR / JobId，请同步状态或查看 FlinkDeployment 事件。")
            else:
                readiness = "ok" if st in ("running", "finished", "failed", "cancelled") or fjid or op_dep else "neutral"
    elif mode == "kubernetes_application":
        if not img:
            readiness = "blocked"
            hints.append("未配置 K8s Application 作业镜像（「系统管理 → 集成」或 FLINK_K8S_APPLICATION_IMAGE），提交将被拒绝。")
        else:
            hints.append(
                "Application 提交时 SQL Gateway 须在容器内能访问 Kubernetes API；Docker Desktop 下 kubeconfig 常含 "
                "https://127.0.0.1:<随机端口>，需在集群侧或宿主侧换成 kubernetes.docker.internal:6443（或可达 apiserver），"
                "或对 GIDO 后端 挂载修正后的 kubeconfig（见 backend/docker-entrypoint.sh）。"
            )
            kc = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
            if cid and not fjid:
                readiness = "warning"
                if not jmrest and not tmpl and not kc:
                    hints.append(
                        "已创建 Application 集群但尚未回填 Job ID：请在集成页配置 JM REST 模板（含 {cluster_id}）或环境变量 FLINK_K8S_APPLICATION_JM_REST_TEMPLATE，"
                        "或为 backend 提供可读 kubeconfig（FLINK_K8S_KUBECONFIG_PATH 或挂载到 /root/.kube/host-kubeconfig）以自动解析 NodePort。"
                    )
                elif not jmrest and not tmpl and kc:
                    hints.append(
                        "已配置 kubeconfig，平台会尝试用 NodePort 访问 JM；若仍无 JobId，请查 REST 是否 NodePort、命名空间是否与 Flink 一致。"
                    )
                else:
                    hints.append("集群已创建，Job ID 待回填：可打开 Flink Web UI 总览观察；长时间无 Job 请查 Gateway / Kubernetes 事件与镜像拉取。")
            elif st == "running" and not cid and not fjid:
                readiness = "warning"
                hints.append("平台为 running 但未记录 clusterId/JobId，请在作业运维中同步状态或查看诊断。")
            else:
                readiness = "ok" if st in ("running", "finished", "failed", "cancelled") or fjid or cid else "neutral"
                if fjid and cid and not tmpl and not kc:
                    hints.append("建议在集成页配置 JM REST 模板，或挂载 kubeconfig 以自动解析 NodePort。")
    else:
        if st == "draft":
            readiness = "neutral"
        elif st == "running" and not fjid:
            readiness = "warning"
            hints.append("平台为 running 但未记录 Flink Job ID：请点「同步状态」或查看最近一次提交警告 / SQL Gateway 日志。")
        else:
            readiness = "ok"
    if len(hints) > 5:
        hints = hints[:5]
    return {"readiness": readiness, "hints": hints, "submit_mode": mode}


def flink_ui_base_from_runtime_cfg(cfg: FlinkRuntimeConfig) -> str:
    """与 flink_ui_base_url 一致，但基于合并后的 FlinkRuntimeConfig（含 Session Profile）。"""
    explicit = (cfg.flink_ui_url or "").strip().rstrip("/")
    if explicit:
        return explicit
    jm = (cfg.flink_url or "").strip().rstrip("/")
    if not jm:
        return ""
    try:
        h = (urlparse(jm).hostname or "").lower()
        if h == "host.docker.internal":
            return ""
    except Exception:
        pass
    return jm


def flink_ui_base_url() -> str:
    """用户浏览器打开的 JobManager/Web UI 基底：须与 FLINK_UI_URL 一致或可公网/本机直达。
    未单独配置时仅回退到 jm_base；若 JM 为 host.docker.internal（仅容器内向宿主解析），则不可用，避免生成坏链。"""
    explicit = (flink._ui_url or "").strip().rstrip("/")
    if explicit:
        return explicit
    jm = (flink.jm_base or "").strip().rstrip("/")
    if not jm:
        return ""
    try:
        h = (urlparse(jm).hostname or "").lower()
        if h == "host.docker.internal":
            return ""
    except Exception:
        pass
    return jm


def _jm_rest_url_for_browser(jm_rest: Optional[str]) -> str:
    from app.services.flink_operator_submit import jm_rest_url_for_browser

    return jm_rest_url_for_browser(jm_rest) or (jm_rest or "").strip().rstrip("/")


def flink_job_console_url(
    flink_job_id: Optional[str],
    *,
    application_jm_rest: Optional[str] = None,
    runtime_cfg: Optional[FlinkRuntimeConfig] = None,
    operator_mode: bool = False,
) -> Optional[str]:
    """Classic Flink Dashboard：`/#/job/{id}/overview`。
    Operator / K8s Application：仅用该次部署的 JM REST，禁止回退 Session 的 FLINK_UI_URL。
    """
    app_jm = _jm_rest_url_for_browser(application_jm_rest)
    jid = (str(flink_job_id).strip() if flink_job_id else "") or ""
    if jid:
        if app_jm:
            return f"{app_jm}/#/job/{jid}/overview"
        if operator_mode:
            return None
        base = flink_ui_base_from_runtime_cfg(runtime_cfg) if runtime_cfg is not None else flink_ui_base_url()
        if not base:
            return None
        return f"{base}/#/job/{jid}/overview"
    if app_jm:
        return f"{app_jm}/#/overview"
    return None


def _sql_gateway_response_error_detail(status: int, body: str) -> str:
    """
    将 SQL Gateway 错误响应整理为可读正文。
    500 时 body 常为 {"errors":["Internal server error.","<Exception ... stack ...>"]}，仅截断前 4k 会丢掉 Caused by。
    """
    raw = body or ""
    if len(raw) > 24000:
        raw = raw[:24000] + "\n…(response truncated)"

    core = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = None

    if isinstance(obj, dict):
        errs = obj.get("errors")
        if isinstance(errs, list):
            parts: List[str] = []
            for e in errs:
                if e is None:
                    continue
                s = str(e).strip()
                if s:
                    parts.append(s)
            if parts:
                core = "\n\n".join(parts)

    low = core.lower()
    hints: List[str] = []
    if "forbidden" in low or " is forbidden" in low:
        hints.append(
            "【Gateway 响应中含 Forbidden】多为命名空间内 RBAC：对照下方堆栈里的 resource/verb，"
            "补全 `k8s/legacy/flink.yaml` 中 `flink-sql-gateway-application` Role 后 `kubectl apply` 并 rollout sql-gateway。"
        )
    if "unauthorized" in low or ("401" in low and "response code" in low):
        hints.append("【Unauthorized / 401】kubeconfig 凭据过期、上下文错误，或集群拒绝当前身份。")
    if "unknownhostexception" in low and "kubernetes.default.svc" in low:
        hints.append(
            "【UnknownHost kubernetes.default.svc】Flink 用 Configuration 的 kubernetes.config.file 建 Fabric8 客户端，"
            "默认是 ~/.kube/config，不会用 KUBECONFIG。"
            " 请对集群应用最新 `k8s/legacy/flink.yaml`（init 写 kubeconfig + 主容器复制到 /opt/flink/.kube/config，并带 "
            "-Dkubernetes.config.file=… 与 -Dkubernetes.namespace=flink），再 rollout sql-gateway。"
        )
    if "connection refused" in low or "failed to connect to" in low or "unknown host" in low:
        hints.append(
            "【无法连接 Kubernetes API】常见于容器内 kubeconfig 仍指向 https://127.0.0.1:<随机端口>；"
            "请将 apiserver 改为 kubernetes.docker.internal:6443（或集群可达地址），或由 backend/docker-entrypoint.sh 在挂载 host-kubeconfig 时自动替换。"
        )
    if "timeout" in low or "timed out" in low:
        hints.append("【超时】apiserver 或网络慢；也可能是防火墙未放行 Gateway Pod 到 6443。")

    if hints:
        return "\n".join(hints) + f"\n\nHTTP {status}\n{core}"
    return f"HTTP {status}\n{core}"


def _application_deploy_error_hint(detail: str) -> str:
    """K8s Application：Gateway deploy script 500 时的可读说明（常见为容器内访问不到 apiserver）。"""
    return (
        f"{detail}\n\n"
        "【Application / deploy script 常见原因】\n"
        "1) SQL Gateway 跑在 Docker 里时，kubeconfig 里 https://127.0.0.1:<端口>（Docker Desktop 常为随机端口如 53515）"
        " 在容器内指向容器自身。将 kubeconfig 挂到 GIDO 后端 的 /root/.kube/host-kubeconfig 时，entrypoint 会用 sed 把 127.0.0.1:<任意端口> 换成 https://kubernetes.docker.internal:6443。"
        " 若不需要替换，设 GIDO_FLINK_K8S_API_PATCH=disable。\n"
        "2) 未向 backend 提供 kubeconfig：请设 FLINK_K8S_KUBECONFIG_PATH，或自建 compose override / docker run -v 挂载到 /root/.kube/host-kubeconfig。\n"
        "3) 镜像拉取失败、命名空间无权限、资源不足：在目标 namespace 执行 `kubectl get events` 排查。\n"
        "4) SQL Gateway 跑在集群内 Pod 时：须为 Gateway SA 绑定可创建 Deployment/Service/Secret 及 **Role+RoleBinding** 的 Role（见 k8s/legacy/flink.yaml 中 flink-sql-gateway-application）；"
        " 缺「创建 rolebindings」权限时 apiserver 为 Forbidden，Gateway 只显示 deploy script 500。应用最新清单后 rollout sql-gateway。\n"
        "5) 堆栈含 UnknownHostException: kubernetes.default.svc：Flink 默认读 ~/.kube/config 建 K8s 客户端（不用 KUBECONFIG）；"
        "本仓库 `k8s/legacy/flink.yaml` 由 init 写 kubeconfig（apiserver 为 IP:443）并 `-Dkubernetes.config.file=/opt/flink/.kube/config`，请 apply + rollout。\n"
        "6) 仍失败：`kubectl logs -n flink deploy/flink-sql-gateway --tail=200` 搜 Caused by / Forbidden；`kubectl get events -n flink --sort-by=.lastTimestamp | tail -40`。\n"
    )


def _explain_sql_gateway_connect_error(base: str, cause: Exception) -> str:
    """human-readable 指引：host.docker.internal / 宿主 PyCharm vs Docker 内网等。"""
    low = base.lower()
    chunks: List[str] = []
    details = str(cause)
    linux_unreachable = "[errno 101]" in details.lower() or "network is unreachable" in details.lower()
    refused = "[errno 111]" in details.lower() or "connection refused" in details.lower()

    if refused:
        chunks.append(
            "【未监听】目标地址上无进程监听。"
            " 请确认 Flink SQL Gateway 已按仓库根 k8s/legacy/flink.yaml 部署且可从后端访问（Ingress / NodePort / port-forward），"
            " 再 curl `…/v1/info`；仍失败则 kubectl logs -n flink deploy/flink-sql-gateway。"
            " 若在「系统管理 → 集成」里覆盖过 Gateway URL，请核对或清空库内覆盖。"
        )

    if "host.docker.internal" in low:
        chunks.append(
            "【先试这个】你把 FLINK_SQL_GATEWAY_URL 指到了 host.docker.internal。"
            " 若 GIDO 后端 跑在 Linux/macOS 宿主机（PyCharm、本机 uvicorn），"
            "请改为 http://127.0.0.1:8083（与 compose 默认宿主映射一致）。"
            " host.docker.internal 主要给「容器内的进程访问宿主机」用；在宿主机本进程里常被路由成不可达（如 Errno 101 ENETUNREACH）。"
        )
        chunks.append(
            "若 backend 与 Gateway 同属 Kubernetes：可用集群 DNS，例如 http://flink-sql-gateway.flink.svc.cluster.local:8083。"
        )
    elif linux_unreachable:
        chunks.append(
            "【Network unreachable】多为地址/路由选错。"
            " 本机后端请改用 127.0.0.1 与 Ingress/NodePort 映射端口；集群内请用 Service DNS。"
        )
    else:
        chunks.append(
            "确认 SQL Gateway 已启动。"
            " PyCharm 本机后端常用 http://127.0.0.1:<Ingress 或 NodePort 映射端口>；"
            " Docker 内后端请填可被该容器解析并访问的 URL（见 k8s/flink-sql-gateway-ingress.yaml）。"
        )
    chunks.append(f"详情: 无法访问「{base}」—— {cause}")
    return "\n".join(chunks)


def _explain_flink_jm_connect_error(jm_base: str, path: str, cause: Exception) -> str:
    """将 urllib3 RemoteDisconnected / ConnectionError 转成人话（常见：JM 重启、8081 争用、指错端口）。"""
    base = (jm_base or "").strip().rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    url = f"{base}{p}" if base else p
    det = str(cause)
    low = det.lower()
    remote_gone = (
        "remotedisconnected" in det
        or "remote end closed connection" in low
        or "connection aborted" in low
        or "connection reset" in low
    )
    chunks: List[str] = [f"JobManager: {cause}", f"请求 URL: {url}"]
    if remote_gone:
        chunks.append(
            "说明：对端在未返回完整 HTTP 响应前关闭了连接，多见于：① JobManager 正在重启或 OOM（kubectl logs -n flink deploy/flink-jobmanager）；"
            "② 宿主 8081 被其它进程占用或与 LoadBalancer 冲突；"
            "③ JM 尚未就绪，稍后重试。"
            " 请确认仅一套 Session 暴露该 REST，并在集成页填写与后端路由一致的 JM URL。"
        )
        if base:
            chunks.append(f'自检：curl -sS -m8 "{base}/overview"')
    return "\n".join(chunks)


def _parse_version_id_list(raw) -> List[int]:
    """解析作业上绑定的版本 id 列表（JSON 数组或已是 list）。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        out: List[int] = []
        for x in raw:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for x in data:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _dump_version_id_list(ids) -> Optional[str]:
    parsed = _parse_version_id_list(ids)
    if not parsed:
        return None
    return json.dumps(parsed, separators=(",", ":"))


def _streaming_job_public_dict(
    db: Session,
    job: StreamingJob,
    *,
    username_by_id: Optional[dict] = None,
    runtime_cfg: Optional[FlinkRuntimeConfig] = None,
    profile_name: Optional[str] = None,
) -> dict:
    fjid = job.flink_job_id or None
    oid = getattr(job, "owner_id", None) or job.created_by
    uname = None
    if oid:
        if username_by_id is not None:
            uname = username_by_id.get(int(oid))
        else:
            u = db.query(User).filter(User.id == oid).first()
            uname = u.username if u else None
    lsub = getattr(job, "last_submitted_by", None)
    lsub_name = None
    if lsub:
        if username_by_id is not None:
            lsub_name = username_by_id.get(int(lsub))
        else:
            u2 = db.query(User).filter(User.id == lsub).first()
            lsub_name = u2.username if u2 else None
    cfg = runtime_cfg if runtime_cfg is not None else _flink_runtime_cfg_for_job(db, job)
    pid = getattr(job, "flink_session_profile_id", None)
    op_dep = _operator_deployment_name_for_job(job)
    pf_hint = None
    browser_jm = getattr(job, "flink_application_jm_rest", None)
    jar_mode = _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None))
    is_op_jar = bool(op_dep) or (job.job_type == "JAR" and jar_mode == "flink_operator")
    sql_mode_early = _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None))
    if not op_dep and job.job_type == "SQL" and sql_mode_early == "flink_operator":
        from app.services.flink_operator_submit import sql_deployment_name_for_job

        op_dep = (
            getattr(job, "flink_operator_deployment_name", None) or ""
        ).strip() or sql_deployment_name_for_job(job.id, int(getattr(job, "workspace_id", None) or 0))
    if op_dep:
        from app.services.flink_operator_submit import (
            browser_jm_base_for_deployment,
            operator_jm_k8s_service_name,
            operator_ui_port_forward_hint,
        )

        ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
        browser_jm = browser_jm_base_for_deployment(
            op_dep, ns, getattr(job, "flink_application_jm_rest", None), job_id=int(job.id)
        ) or browser_jm
        pf_hint = operator_ui_port_forward_hint(op_dep, ns, browser_jm)
        k8s_svc = operator_jm_k8s_service_name(op_dep, ns)
    else:
        k8s_svc = None
    sql_mode = sql_mode_early
    is_op_sql = job.job_type == "SQL" and sql_mode == "flink_operator"
    is_op_any = is_op_jar or is_op_sql
    console_fjid = fjid
    if is_op_any and op_dep:
        from app.services.flink_operator_submit import resolve_live_flink_job_id

        ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
        live = resolve_live_flink_job_id(op_dep, ns, stored=fjid, job_id=int(job.id))
        if live and live != (fjid or ""):
            console_fjid = live
    console_mode = (
        "operator"
        if is_op_any
        else (
            "k8s_application"
            if sql_mode == "kubernetes_application"
            else "session"
        )
    )
    return {
        "id": job.id,
        "name": job.name,
        "job_type": job.job_type,
        "status": job.status,
        "flink_job_id": fjid,
        "flink_sql_submit_mode": _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)),
        "flink_jar_submit_mode": _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None)),
        "flink_operator_deployment_name": getattr(job, "flink_operator_deployment_name", None),
        "flink_application_cluster_id": getattr(job, "flink_application_cluster_id", None),
        "flink_application_jm_rest": getattr(job, "flink_application_jm_rest", None),
        "flink_session_profile_id": int(pid) if pid is not None else None,
        "flink_session_profile_name": profile_name,
        "flink_console_url": flink_job_console_url(
            console_fjid,
            application_jm_rest=browser_jm if is_op_any else getattr(job, "flink_application_jm_rest", None),
            runtime_cfg=cfg,
            operator_mode=is_op_any,
        ),
        "flink_console_mode": console_mode,
        "flink_k8s_jm_service": k8s_svc,
        "flink_ui_port_forward_hint": pf_hint,
        "last_submit_error": job.last_submit_error,
        "last_submitted_at": getattr(job, "last_submitted_at", None),
        "last_submitted_by": getattr(job, "last_submitted_by", None),
        "last_submitted_by_username": lsub_name,
        "current_approved_release_id": getattr(job, "current_approved_release_id", None),
        "current_running_release_id": getattr(job, "current_running_release_id", None),
        "lifecycle_state": getattr(job, "lifecycle_state", None) or "draft",
        "parallelism": job.parallelism,
        "streaming_properties": getattr(job, "streaming_properties", None),
        "folder_id": job.folder_id,
        "sort_order": getattr(job, "sort_order", 0) or 0,
        "jar_artifact_id": getattr(job, "jar_artifact_id", None),
        "jar_version_id": getattr(job, "jar_version_id", None),
        "connector_version_ids": _parse_version_id_list(getattr(job, "connector_version_ids", None)),
        "dependency_file_version_ids": _parse_version_id_list(getattr(job, "dependency_file_version_ids", None)),
        "definition_kind": getattr(job, "definition_kind", None) or ("jar" if job.job_type == "JAR" else "sql"),
        "pipeline_spec": getattr(job, "pipeline_spec", None),
        "compiler_version": getattr(job, "compiler_version", None),
        "generated_artifact": getattr(job, "generated_artifact", None),
        "spec_hash": getattr(job, "spec_hash", None),
        "script_content": job.script_content,
        "main_class": job.main_class,
        "program_args": job.program_args,
        "jar_path": job.jar_path,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "created_by": job.created_by,
        "owner_id": oid,
        "owner_username": uname,
        "is_locked": bool(getattr(job, "is_locked", False)),
        "flink_operational": _compute_flink_operational(job, runtime_cfg=cfg),
    }


# ==================== 模型 ====================

class StreamingJob(Base):
    __tablename__ = "dw_streaming_jobs"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    name = Column(String(128), nullable=False)
    job_type = Column(String(16), nullable=False)   # SQL / JAR
    script_content = Column(Text)                   # SQL 内容
    jar_path = Column(String(512))                  # JAR 文件路径（容器内）
    main_class = Column(String(256))                # JAR 主类
    program_args = Column(Text)                     # 运行参数（Kafka broker 等长参数，勿限 VARCHAR(512)）
    parallelism = Column(Integer, default=1)
    # Flink SQL Gateway Open Session 的额外 properties（JSON 对象字符串），与 parallelism 等合并后提交
    streaming_properties = Column(Text, nullable=True)
    # flink_operator（默认）：FlinkDeployment + 统一运行时；遗留 session/kubernetes_application 须 GIDO_LEGACY_FLINK_SUBMIT
    flink_sql_submit_mode = Column(String(32), default="flink_operator", nullable=False)
    # JAR：session=Session JM /jars/run；flink_operator=FlinkDeployment CR（生产）
    flink_jar_submit_mode = Column(String(32), default="flink_operator", nullable=False)
    flink_operator_deployment_name = Column(String(128), nullable=True)
    flink_application_cluster_id = Column(String(256), nullable=True)
    flink_application_jm_rest = Column(String(512), nullable=True)
    # 选用工作空间下某套 Flink Session 配置；NULL 表示沿用「环境 + 平台集成」默认
    flink_session_profile_id = Column(Integer, ForeignKey("dw_flink_session_profiles.id"), nullable=True)
    flink_job_id = Column(String(64))               # Flink 返回的 jobId
    last_submit_error = Column(Text, nullable=True)  # 最近一次提交到 Flink 失败时的栈/错误原文
    last_submitted_at = Column(DateTime, nullable=True)
    last_submitted_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    status = Column(String(32), default="draft")    # draft/running/finished/failed/cancelled
    folder_id = Column(Integer, ForeignKey("dw_node_folders.id"), nullable=True)
    sort_order = Column(Integer, default=0)  # 同目录内拖拽排序
    # 工作空间 JAR 制品库绑定（优先于遗留 jar_path）
    jar_artifact_id = Column(Integer, nullable=True)
    jar_version_id = Column(Integer, nullable=True)
    # JSON 数组字符串：连接器版本 id / 依赖文件版本 id
    connector_version_ids = Column(Text, nullable=True)
    dependency_file_version_ids = Column(Text, nullable=True)
    # 兼容 SQL/JAR 草稿；pipeline 使用版本化 typed spec 与确定性编译产物。
    definition_kind = Column(String(32), nullable=False, default="sql")
    pipeline_spec = Column(JSON, nullable=True)
    compiler_version = Column(String(64), nullable=True)
    generated_artifact = Column(JSON, nullable=True)
    spec_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"))
    owner_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    is_locked = Column(Boolean, default=False, nullable=False)
    # 发布/运行指针与旧 status 并存；status 继续服务现有 Flink 状态同步。
    current_approved_release_id = Column(
        Integer, ForeignKey("dw_streaming_job_releases.id"), nullable=True
    )
    current_running_release_id = Column(
        Integer, ForeignKey("dw_streaming_job_releases.id"), nullable=True
    )
    lifecycle_state = Column(String(32), default="draft", nullable=False)


class StreamingJobHistory(Base):
    """实时作业逻辑快照（保存 / 提交 SQL 前写入），对齐数据开发 dw_node_history。"""
    __tablename__ = "dw_streaming_job_history"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("dw_streaming_jobs.id", ondelete="CASCADE"), nullable=False)
    job_type = Column(String(16), nullable=False)
    script_content = Column(Text, nullable=True)
    main_class = Column(String(256), nullable=True)
    program_args = Column(Text, nullable=True)
    parallelism = Column(Integer, nullable=True)
    streaming_properties = Column(Text, nullable=True)
    flink_sql_submit_mode = Column(String(32), nullable=True)
    flink_jar_submit_mode = Column(String(32), nullable=True)
    saved_at = Column(DateTime, default=datetime.utcnow)
    saved_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)


class StreamingJobRelease(Base):
    """已提交的不可变发布快照；审批字段不属于快照内容，可独立更新。"""

    __tablename__ = "dw_streaming_job_releases"
    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_streaming_release_job_version"),
        Index("idx_streaming_release_job_submitted", "job_id", "submitted_at"),
    )
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("dw_streaming_jobs.id", ondelete="CASCADE"), nullable=False
    )
    version = Column(Integer, nullable=False)
    job_name = Column(String(128), nullable=False)
    job_type = Column(String(16), nullable=False)
    script_content = Column(Text, nullable=True)
    jar_path = Column(String(512), nullable=True)
    main_class = Column(String(256), nullable=True)
    program_args = Column(Text, nullable=True)
    parallelism = Column(Integer, nullable=False, default=1)
    streaming_properties = Column(Text, nullable=True)
    flink_sql_submit_mode = Column(String(32), nullable=True)
    flink_jar_submit_mode = Column(String(32), nullable=True)
    flink_session_profile_id = Column(Integer, nullable=True)
    jar_artifact_id = Column(Integer, nullable=True)
    jar_version_id = Column(Integer, nullable=True)
    connector_version_ids = Column(Text, nullable=True)
    dependency_file_version_ids = Column(Text, nullable=True)
    definition_kind = Column(String(32), nullable=False, default="sql")
    pipeline_spec = Column(JSON, nullable=True)
    compiler_version = Column(String(64), nullable=True)
    generated_artifact = Column(JSON, nullable=True)
    spec_hash = Column(String(64), nullable=True)
    content_hash = Column(String(64), nullable=False)
    release_note = Column(Text, nullable=True)
    approval_status = Column(String(24), nullable=False, default="pending")
    submitted_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    approved_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_comment = Column(Text, nullable=True)


class StreamingRestorePoint(Base):
    """可跨进程恢复使用的 checkpoint/savepoint 目录。"""

    __tablename__ = "dw_streaming_restore_points"
    __table_args__ = (
        Index("idx_streaming_restore_job_created", "job_id", "created_at"),
    )
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("dw_streaming_jobs.id", ondelete="CASCADE"), nullable=False
    )
    release_id = Column(
        Integer, ForeignKey("dw_streaming_job_releases.id"), nullable=True
    )
    operation_id = Column(Integer, nullable=True)
    point_type = Column(String(24), nullable=False, default="savepoint")
    status = Column(String(24), nullable=False, default="pending")
    path = Column(String(2048), nullable=True)
    flink_job_id = Column(String(64), nullable=True)
    trigger_reason = Column(String(64), nullable=True)
    metadata_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class StreamingOperation(Base):
    """部署、停止、重启与恢复等生命周期操作的持久审计记录。"""

    __tablename__ = "dw_streaming_operations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_streaming_operation_idempotency"),
        Index("idx_streaming_operation_job_requested", "job_id", "requested_at"),
    )
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(
        Integer, ForeignKey("dw_streaming_jobs.id", ondelete="CASCADE"), nullable=False
    )
    release_id = Column(
        Integer, ForeignKey("dw_streaming_job_releases.id"), nullable=True
    )
    restore_point_id = Column(
        Integer, ForeignKey("dw_streaming_restore_points.id"), nullable=True
    )
    operation_type = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    idempotency_key = Column(String(128), nullable=True)
    requested_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    flink_job_id = Column(String(64), nullable=True)
    flink_deployment_name = Column(String(128), nullable=True)
    restore_path = Column(String(2048), nullable=True)
    request_json = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)


_STREAMING_RELEASE_MODEL_SNAPSHOT_FIELDS = (
    "job_name",
    "job_type",
    "script_content",
    "jar_path",
    "main_class",
    "program_args",
    "parallelism",
    "streaming_properties",
    "flink_sql_submit_mode",
    "flink_jar_submit_mode",
    "flink_session_profile_id",
    "jar_artifact_id",
    "jar_version_id",
    "connector_version_ids",
    "dependency_file_version_ids",
    "definition_kind",
    "pipeline_spec",
    "compiler_version",
    "generated_artifact",
    "spec_hash",
    "content_hash",
    "release_note",
)


@event.listens_for(StreamingJobRelease, "before_update")
def _prevent_streaming_release_snapshot_update(mapper, connection, target) -> None:
    """允许审批元数据变化，但拒绝修改已提交的发布内容。"""
    state = sa_inspect(target)
    changed = [
        name
        for name in _STREAMING_RELEASE_MODEL_SNAPSHOT_FIELDS
        if state.attrs[name].history.has_changes()
    ]
    if changed:
        raise ValueError(f"streaming release snapshot is immutable: {', '.join(changed)}")


class StreamingJarArtifact(Base):
    """工作空间级 JAR 制品（多版本）。"""
    __tablename__ = "dw_streaming_jar_artifacts"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StreamingJarVersion(Base):
    """JAR 制品版本（二进制 + 审计）。"""
    __tablename__ = "dw_streaming_jar_versions"
    id = Column(Integer, primary_key=True, index=True)
    artifact_id = Column(Integer, ForeignKey("dw_streaming_jar_artifacts.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    file_name = Column(String(256), nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    storage_key = Column(String(512), nullable=True)
    default_main_class = Column(String(256), nullable=True)
    change_note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="active")  # active | deprecated
    uploaded_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class StreamingConnectorArtifact(Base):
    """工作空间级连接器制品（多版本，.jar）。"""
    __tablename__ = "dw_streaming_connector_artifacts"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StreamingConnectorVersion(Base):
    __tablename__ = "dw_streaming_connector_versions"
    id = Column(Integer, primary_key=True, index=True)
    artifact_id = Column(Integer, ForeignKey("dw_streaming_connector_artifacts.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    file_name = Column(String(256), nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    storage_key = Column(String(512), nullable=True)
    change_note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    uploaded_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class StreamingFileArtifact(Base):
    """工作空间级依赖文件制品（多版本，任意文件）。"""
    __tablename__ = "dw_streaming_file_artifacts"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StreamingFileVersion(Base):
    __tablename__ = "dw_streaming_file_versions"
    id = Column(Integer, primary_key=True, index=True)
    artifact_id = Column(Integer, ForeignKey("dw_streaming_file_artifacts.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    file_name = Column(String(256), nullable=False)
    size_bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    storage_key = Column(String(512), nullable=True)
    change_note = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    uploaded_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


def _append_streaming_job_history_snapshot(db: Session, job: StreamingJob, user_id: int) -> None:
    db.add(
        StreamingJobHistory(
            job_id=job.id,
            job_type=job.job_type or "SQL",
            script_content=job.script_content,
            main_class=job.main_class,
            program_args=job.program_args,
            parallelism=job.parallelism,
            streaming_properties=getattr(job, "streaming_properties", None),
            flink_sql_submit_mode=_normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)),
            flink_jar_submit_mode=_normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None)),
            saved_by=user_id,
        )
    )


# ==================== Flink 客户端 ====================

class FlinkClient:
    def __init__(self):
        self.apply_runtime(
            None,
            None,
            None,
            None,
            k8s_application_image=_FLINK_K8S_RT_UNDEF,
            k8s_namespace=_FLINK_K8S_RT_UNDEF,
            k8s_application_jm_rest_template=_FLINK_K8S_RT_UNDEF,
        )

    def apply_runtime(
        self,
        jm_url: Optional[str],
        gw_url: Optional[str],
        gwjm_url: Optional[str],
        ui_url: Optional[str],
        *,
        k8s_application_image: object = _FLINK_K8S_RT_UNDEF,
        k8s_namespace: object = _FLINK_K8S_RT_UNDEF,
        k8s_application_jm_rest_template: object = _FLINK_K8S_RT_UNDEF,
    ) -> None:
        """由 flink_runtime.refresh_flink_client 注入；None/空串表示回退 settings（REST）；K8s 字段 UNDEF 表示仅 settings。"""
        jm = (jm_url or "").strip() or (settings.FLINK_URL or "").strip()
        gw = (gw_url or "").strip() or (settings.FLINK_SQL_GATEWAY_URL or "").strip()
        gwjm = (gwjm_url or "").strip() if gwjm_url else ""
        if not gwjm:
            gwjm = (settings.FLINK_GATEWAY_JOBMANAGER_REST_URL or "").strip()
        ui = (ui_url or "").strip() if ui_url else ""
        if not ui:
            ui = (settings.FLINK_UI_URL or "").strip()
        self.jm_base = jm.rstrip("/")
        self._sql_gateway_url = gw.rstrip("/") if gw else ""
        self._gateway_jm_rest_url = gwjm.rstrip("/") if gwjm else None
        self._ui_url = ui.rstrip("/") if ui else None

        if k8s_application_image is _FLINK_K8S_RT_UNDEF:
            self._rt_k8s_application_image = (settings.FLINK_K8S_APPLICATION_IMAGE or "").strip()
        else:
            self._rt_k8s_application_image = (k8s_application_image or "").strip()  # type: ignore[operator]

        if k8s_namespace is _FLINK_K8S_RT_UNDEF:
            ns = (settings.FLINK_K8S_NAMESPACE or "").strip()
            self._rt_k8s_namespace = ns if ns else None
        else:
            ns2 = (k8s_namespace or "").strip() if k8s_namespace is not None else ""  # type: ignore[union-attr]
            self._rt_k8s_namespace = ns2 if ns2 else None

        if k8s_application_jm_rest_template is _FLINK_K8S_RT_UNDEF:
            tpl = (settings.FLINK_K8S_APPLICATION_JM_REST_TEMPLATE or "").strip()
            self._rt_k8s_application_jm_rest_template = tpl if tpl else None
        else:
            t2 = (k8s_application_jm_rest_template or "").strip() if k8s_application_jm_rest_template is not None else ""  # type: ignore[union-attr]
            self._rt_k8s_application_jm_rest_template = t2 if t2 else None

    def k8s_application_image(self) -> str:
        return (self._rt_k8s_application_image or "").strip()

    def k8s_namespace(self) -> Optional[str]:
        return self._rt_k8s_namespace

    def k8s_namespace_resolved(self) -> str:
        return (self._rt_k8s_namespace or "").strip() or "flink"

    def k8s_application_jm_rest_template(self) -> str:
        return (self._rt_k8s_application_jm_rest_template or "").strip()

    def gateway_base_or_raise(self) -> str:
        u = self._sql_gateway_url
        if not (u and u.strip()):
            raise RuntimeError(
                "未配置 Flink SQL Gateway。"
                "Flink SQL 必须通过 SQL Gateway 提交（REST 含 /v1/sessions），不能与 JobManager 的 FLINK_URL（通常为 :8081）混用。"
                "请在「系统管理 → 集成」或环境变量中设置 Gateway 地址，例如：FLINK_SQL_GATEWAY_URL=http://127.0.0.1:8083"
            )
        return u.strip().rstrip("/")

    def _get(self, path: str) -> dict:
        url = f"{self.jm_base}{path}"
        try:
            r = _FLINK_HTTP_GET.get(url, timeout=10)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(self.jm_base, path, e)) from e
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict = None, data=None, files=None, timeout: int = 120) -> dict:
        url = f"{self.jm_base}{path}"
        try:
            r = requests.post(url, json=json, data=data, files=files, timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(self.jm_base, path, e)) from e
        if not r.ok:
            raise RuntimeError(f"Flink HTTP {r.status_code}: {r.text[:4000]}")
        return r.json()

    def _patch(self, path: str, json: dict = None) -> dict:
        url = f"{self.jm_base}{path}"
        try:
            r = requests.patch(url, json=json, timeout=10)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(self.jm_base, path, e)) from e
        r.raise_for_status()
        return r.json()

    def _gw_get(self, path: str, timeout: int = 10) -> dict:
        base = self.gateway_base_or_raise()
        try:
            r = _FLINK_HTTP_GET.get(f"{base}{path}", timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_sql_gateway_connect_error(base, e)) from e
        r.raise_for_status()
        return r.json()

    def _gw_post(self, path: str, json: dict = None, timeout: int = 120) -> dict:
        base = self.gateway_base_or_raise()
        try:
            r = requests.post(f"{base}{path}", json=json, timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_sql_gateway_connect_error(base, e)) from e
        if not r.ok:
            det = _sql_gateway_response_error_detail(r.status_code, r.text)
            raise RuntimeError(f"Flink SQL Gateway HTTP {r.status_code}: {det}")
        return r.json()

    def gateway_execution_config_pointing_at_jm(self) -> dict:
        """
        Gateway 与其它进程通信时提交的 Flink REST 坐标（必须由 SQL Gateway 进程能连通）。
        见 https://nightlies.apache.org/flink/flink-docs-stable/docs/dev/table/sql-gateway/overview/
        """
        raw = (self._gateway_jm_rest_url or "").strip()
        if not raw:
            jm_base = (self.jm_base or "").strip()
            if not jm_base:
                raise RuntimeError("缺少 FLINK_URL（或 FLINK_GATEWAY_JOBMANAGER_REST_URL），无法告诉 SQL Gateway 连接哪套 JobManager")
            jm_u = urlparse(jm_base if "://" in jm_base else f"http://{jm_base}")
            host = (jm_u.hostname or "").lower()
            if host in ("127.0.0.1", "localhost", "::1"):
                # 后端常用回环访问 JM；SQL Gateway 若在 K8s 内，须用集群 DNS 指向 Session JM Service
                raw = "http://flink-jobmanager.flink.svc.cluster.local:8081"
                logger.info(
                    "FLINK_GATEWAY_JOBMANAGER_REST_URL 未设置且 FLINK_URL 为回环地址："
                    "对 Gateway 使用 %s（与本仓库 k8s/legacy/flink.yaml 命名空间 flink 一致；"
                    "若 JM Service 名或命名空间不同请显式设置 FLINK_GATEWAY_JOBMANAGER_REST_URL）",
                    raw,
                )
            else:
                raw = jm_base
        u = urlparse(raw if "://" in raw else f"http://{raw}")
        host = u.hostname
        if not host:
            raise RuntimeError(f"无法从 JobManager REST 地址解析主机名: {raw!r}")
        port = u.port
        if port is None:
            port = 443 if (u.scheme or "http") == "https" else 8081
        cfg = {"rest.address": host, "rest.port": str(port)}
        return cfg

    def overview(self) -> dict:
        return self._get("/overview")

    def list_jobs(self) -> list:
        return self._get("/jobs/overview").get("jobs", [])

    def fetch_job_document(
        self, job_id: str, jm_base: Optional[str] = None, *, timeout: float = 10
    ) -> Optional[dict]:
        """GET /jobs/{id}；若在 JobManager 上已不存在返回 None（通常为 404，作业已结束或被撤销并清理记录）。"""
        base = (jm_base or self.jm_base or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("未配置 JobManager REST（FLINK_URL）")
        path = f"/jobs/{job_id}"
        try:
            r = _FLINK_HTTP_GET.get(f"{base}{path}", timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(base, path, e)) from e
        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError(f"Flink HTTP {r.status_code}: {(r.text or '')[:2000]}")
        return r.json()

    def job_detail(self, job_id: str) -> dict:
        d = self.fetch_job_document(job_id)
        if d is None:
            raise RuntimeError(f"Flink 上已无该作业详情 (job id={job_id!r})，可能已从集群结束或卸载")
        return d

    def job_exceptions(self, job_id: str, jm_base: Optional[str] = None) -> dict:
        base = (jm_base or self.jm_base or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("未配置 JobManager REST（FLINK_URL）")
        path = f"/jobs/{job_id}/exceptions"
        try:
            r = _FLINK_HTTP_GET.get(f"{base}{path}", timeout=15)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(base, path, e)) from e
        r.raise_for_status()
        return r.json()

    def job_checkpoints(self, job_id: str, jm_base: Optional[str] = None, *, timeout: float = 10) -> dict:
        """GET /jobs/{id}/checkpoints。"""
        base = (jm_base or self.jm_base or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("未配置 JobManager REST（FLINK_URL）")
        path = f"/jobs/{job_id}/checkpoints"
        try:
            r = _FLINK_HTTP_GET.get(f"{base}{path}", timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(base, path, e)) from e
        r.raise_for_status()
        return r.json()

    def cancel_job(self, job_id: str, jm_base: Optional[str] = None):
        base = (jm_base or self.jm_base or "").strip().rstrip("/")
        if not base:
            raise RuntimeError("未配置 JobManager REST（FLINK_URL）")
        path = f"/jobs/{job_id}"
        try:
            r = requests.patch(f"{base}{path}", json={"cancel-job": True}, timeout=15)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(_explain_flink_jm_connect_error(base, path, e)) from e
        r.raise_for_status()

    def upload_jar(self, file_bytes: bytes, filename: str) -> str:
        """上传 JAR，返回 jar_id"""
        resp = self._post("/jars/upload", files={"jarfile": (filename, file_bytes, "application/java-archive")})
        # 返回格式: {"filename": "/tmp/flink-web-xxx/flink-web-upload/xxx_filename.jar", "status": "success"}
        return resp.get("filename", "")

    def run_jar(self, jar_id: str, main_class: str = None, args: str = None, parallelism: int = 1) -> str:
        """提交 JAR 任务，返回 jobId"""
        payload = {"parallelism": parallelism}
        if main_class:
            payload["entryClass"] = main_class
        if args:
            payload["programArgsList"] = args.split()
        path_id = quote(str(jar_id).strip(), safe="")
        resp = self._post(f"/jars/{path_id}/run", json=payload)
        jid = (resp.get("jobid") or resp.get("jobId") or "").strip()
        if not jid:
            raise RuntimeError(f"Flink /jars/run 未返回 jobid: {resp}")
        return jid

    @staticmethod
    def _split_sql_statements(sql: str) -> List[str]:
        """按 `;` 拆成多条语句（与 Gateway 单次 execute 对齐）。不包含分号的字符串常量一般无影响。"""
        out: List[str] = []
        for chunk in re.split(r"\s*;\s*", sql.strip()):
            line = chunk.strip()
            if not line or line.startswith("--"):
                continue
            out.append(line)
        return out

    def _list_jm_job_ids(self) -> Set[str]:
        """当前 JM 上已知的 jobId 集合（用于提交前后差分，兼容 Flink 2.x Gateway fetchResults 异常）。"""
        try:
            data = self._get("/jobs/overview")
            jobs = data.get("jobs") or []
            out: Set[str] = set()
            for j in jobs:
                jid = j.get("jid") or j.get("id")
                if jid:
                    out.add(str(jid))
            return out
        except Exception:
            return set()

    def _wait_new_running_job_id(self, before: Set[str], deadline_s: float = 120.0) -> Optional[str]:
        """在 JM 上出现、且不在 before 中的活跃作业，取一个 jobId（Session 流式 INSERT 回退路径）。"""
        dl = time.monotonic() + deadline_s
        active = (
            "RUNNING",
            "CREATED",
            "RECONCILING",
            "INITIALIZING",
            "RESTARTING",
            "FAILING",
        )
        while time.monotonic() < dl:
            try:
                now = self._list_jm_job_ids()
                for jid in now - before:
                    try:
                        doc = self.fetch_job_document(jid)
                        st = (doc.get("state") or "").upper()
                        if st in active:
                            return jid
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.85)
        return None

    def _observe_new_job_for_gateway_error(
        self, before: Set[str], *, deadline_s: float = 40.0
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Gateway 对 INSERT 偶发 status=ERROR 且 fetchResults 500，真实原因在 JM 侧作业。
        跟踪「before 之后」新出现的第一个 jobId，直至 FAILED/CANCELED、RUNNING，或超时。
        返回 (job_id, state)；超时无新作业则 (None, None)。
        """
        dl = time.monotonic() + deadline_s
        terminal_bad = {"FAILED", "CANCELED", "CANCELLED"}
        target_jid: Optional[str] = None
        while time.monotonic() < dl:
            try:
                now = self._list_jm_job_ids()
                new_ids = sorted(now - before)
                if not new_ids:
                    time.sleep(0.42)
                    continue
                if target_jid is None:
                    target_jid = new_ids[0]
                if target_jid not in now:
                    time.sleep(0.35)
                    continue
                doc = self.fetch_job_document(target_jid)
                st = (doc.get("state") or "").strip().upper()
                if st in terminal_bad:
                    return target_jid, st
                if st == "RUNNING":
                    return target_jid, st
                if st == "FAILING":
                    time.sleep(0.35)
                    continue
            except Exception:
                pass
            time.sleep(0.42)
        if target_jid:
            try:
                doc = self.fetch_job_document(target_jid)
                st = (doc.get("state") or "").strip().upper()
                if st in terminal_bad or st == "FAILING":
                    return target_jid, st
            except Exception:
                pass
        return (None, None)

    def _operation_status(self, session_id: str, op_handle: str) -> str:
        st = self._gw_get(f"/v1/sessions/{session_id}/operations/{op_handle}/status")
        return (st.get("status") or "").upper()

    def _fetch_operation_result_payload(self, session_id: str, op_handle: str, *, strict: bool) -> dict:
        """读取结果第一页；strict 时对非 2xx 抛错（用于 ERROR 态须拿到报错体）。"""
        base = self.gateway_base_or_raise()
        for qp in ("?rowFormat=PLAIN_TEXT", ""):
            try:
                r = _FLINK_HTTP_GET.get(
                    f"{base}/v1/sessions/{session_id}/operations/{op_handle}/result/0{qp}",
                    timeout=60,
                )
            except requests.exceptions.RequestException as e:
                raise RuntimeError(_explain_sql_gateway_connect_error(base, e)) from e
            if r.status_code == 404:
                continue
            if r.ok:
                try:
                    return r.json()
                except Exception:
                    return {"rawText": r.text[:4000]}
            if strict:
                raise RuntimeError(f"Flink SQL Gateway HTTP {r.status_code}: {r.text[:4000]}")
            return {}
        if strict:
            raise RuntimeError("无法读取 operation 结果（result/0 不可用）")
        return {}

    @staticmethod
    def _extract_job_id_from_gateway_result(result: dict) -> Optional[str]:
        rows = result.get("results", {}).get("data", []) or []
        for row in rows:
            fields = row.get("fields", [])
            if fields and fields[0]:
                return str(fields[0])
        return None

    def submit_sql(self, sql: str, parallelism: int = 1, extra_properties: Optional[dict] = None) -> str:
        """
        通过 SQL Gateway 在同一 session 中顺序执行多条语句（每条单独 POST）。
        Flink Gateway 单次 statement 通常为单条 DDL/DML；多句连体提交易导致第 3 条 INSERT 等失败。
        返回 Flink jobId（若 INSERT 结果中有）；否则退回 session_handle。
        """
        statements = self._split_sql_statements(sql)
        if not statements:
            raise RuntimeError("SQL 内容为空或可执行语句数为 0")
        jm_exec = self.gateway_execution_config_pointing_at_jm()

        props = {"execution.runtime-mode": "streaming", "parallelism.default": str(parallelism)}
        split_props, _ = _split_streaming_properties_for_sql(extra_properties or {})
        props.update(split_props)

        session_open_body: dict = {
            "properties": props,
            "executionConfig": jm_exec,
        }
        try:
            session_resp = self._gw_post("/v1/sessions", json=session_open_body)
        except RuntimeError as e:
            err = str(e)
            # 个别 Flink 版本的 Open Session 不认 executionConfig，退化为仅会话级 properties
            if "400" in err or "Bad Request" in err or "UnrecognizedPropertyException" in err:
                logger.info("Gateway OpenSession 附带 executionConfig 失败，重试不包含 executionConfig: %s", err[:400])
                session_resp = self._gw_post("/v1/sessions", json={"properties": props})
            else:
                raise

        session_id = session_resp.get("sessionHandle")
        if not session_id:
            raise RuntimeError("创建 SQL Gateway session 失败")
        last_job_id: Optional[str] = None
        try:
            n = len(statements)
            for idx, stmt in enumerate(statements, start=1):
                before_stmt = self._list_jm_job_ids()
                stmt_body: dict = {
                    "statement": stmt,
                    "executionConfig": jm_exec,
                }
                try:
                    stmt_resp = self._gw_post(
                        f"/v1/sessions/{session_id}/statements",
                        json=stmt_body,
                        timeout=120,
                    )
                except RuntimeError as e:
                    es = str(e)
                    if "400" in es or "Bad Request" in es:
                        logger.info(
                            "Gateway ExecuteStatement executionConfig 可能不被接受，本条语句改用默认集群: %s",
                            es[:400],
                        )
                        stmt_resp = self._gw_post(
                            f"/v1/sessions/{session_id}/statements",
                            json={"statement": stmt},
                            timeout=120,
                        )
                    else:
                        raise
                op_handle = stmt_resp.get("operationHandle")
                if not op_handle:
                    raise RuntimeError(f"第 {idx}/{n} 条语句未返回 operationHandle: {stmt_resp}")
                deadline = time.monotonic() + 300.0
                final_status = "UNKNOWN"
                log_t = 0.0
                while time.monotonic() < deadline:
                    st_raw = self._operation_status(session_id, op_handle)
                    stu = (st_raw or "").strip().upper()
                    final_status = stu
                    if stu == "RUNNING":
                        time.sleep(2.0)
                        final_status = (self._operation_status(session_id, op_handle) or "").strip().upper()
                        break
                    if stu in ("FINISHED", "ERROR", "CANCELED", "CANCELLED"):
                        break
                    now = time.monotonic()
                    if now - log_t > 30.0:
                        logger.info(
                            "SQL Gateway operation 等待中: status=%s idx=%s/%s",
                            st_raw,
                            idx,
                            n,
                        )
                        log_t = now
                    time.sleep(0.85)
                if final_status == "UNKNOWN" or final_status not in (
                    "FINISHED",
                    "ERROR",
                    "RUNNING",
                    "CANCELED",
                    "CANCELLED",
                ):
                    raise RuntimeError(
                        f"第 {idx}/{n} 条语句执行超时或未结束（status={final_status}）。"
                        f" 请查看 Flink SQL Gateway 日志；也可尝试拆成单条 DDL 分步执行。语句片段: {stmt[:220]}"
                    )

                # Flink 2.x：流式 INSERT 常保持 RUNNING，且 fetchResults 可能 500；改从 JM 差分取 jobId
                if final_status == "RUNNING":
                    result = self._fetch_operation_result_payload(session_id, op_handle, strict=False)
                    jid = self._extract_job_id_from_gateway_result(result)
                    if not jid:
                        jid = self._wait_new_running_job_id(before_stmt)
                    if not jid:
                        raise RuntimeError(
                            f"第 {idx}/{n} 条：Gateway 状态 RUNNING 但未解析到 jobId（可检查 JM 日志与 TaskManager）。"
                            f" fetchResults 摘要: {str(result)[:1200]}"
                        )
                    last_job_id = jid
                    continue

                strict_fetch = final_status == "ERROR"
                fetch_err: Optional[str] = None
                try:
                    result = self._fetch_operation_result_payload(
                        session_id, op_handle, strict=strict_fetch
                    )
                except RuntimeError as ex:
                    fetch_err = str(ex)
                    logger.warning(
                        "Gateway fetchResults 异常，将尝试 JM 差分: session=%s op=%s",
                        session_id,
                        op_handle,
                        fetch_err[:600],
                    )
                    result = {}
                rk = (result.get("resultKind") or "").upper()
                if final_status == "ERROR" or "ERROR" in rk:
                    # Flink 2.x：INSERT 后 Gateway 常 ERROR + fetchResults 500，但 JM 上已有 FAILED/RUNNING
                    obs_jid, obs_st = self._observe_new_job_for_gateway_error(before_stmt, deadline_s=42.0)
                    if obs_jid and obs_st in ("FAILED", "CANCELED", "CANCELLED", "FAILING"):
                        try:
                            ex = self.job_exceptions(obs_jid)
                            ex_s = json.dumps(ex, ensure_ascii=False)[:12000]
                        except Exception as ex:
                            ex_s = f"(读取 /jobs/{{id}}/exceptions 失败: {ex})"
                        raise RuntimeError(
                            f"第 {idx}/{n} 条语句失败: {stmt[:500]}\n"
                            f"--- Flink 作业 {obs_jid} 状态={obs_st}（SQL Gateway fetchResults 不可用，已从 JobManager 回落） ---\n"
                            f"{ex_s}"
                        )
                    if obs_jid and obs_st == "RUNNING":
                        last_job_id = obs_jid
                        continue
                    fj = self._wait_new_running_job_id(before_stmt, deadline_s=95.0)
                    detail = result if result else fetch_err
                    if fj:
                        last_job_id = fj
                        continue
                    raise RuntimeError(
                        f"第 {idx}/{n} 条语句失败: {stmt[:500]}\n--- Gateway / JM 回落 ---\n{detail}"
                    )
                jid = self._extract_job_id_from_gateway_result(result)
                if not jid:
                    jid = self._wait_new_running_job_id(before_stmt)
                if jid:
                    last_job_id = jid
            return last_job_id or session_id
        finally:
            try:
                gw = self._sql_gateway_url
                if gw and gw.strip():
                    gb = gw.strip().rstrip("/")
                    try:
                        requests.delete(f"{gb}/v1/sessions/{session_id}", timeout=10)
                    except requests.exceptions.RequestException:
                        logger.debug("关闭 SQL Gateway session 网络失败（可忽略）")
            except Exception:
                logger.debug("关闭 SQL Gateway session 失败（可忽略）", exc_info=True)

    @staticmethod
    def _poll_jm_for_running_job(jm_base: str, deadline_seconds: float = 150.0) -> Optional[str]:
        """Application 集群就绪后，从 JM REST /jobs/overview 取一个活跃 jobId。"""
        base = jm_base.strip().rstrip("/")
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            try:
                r = _FLINK_HTTP_GET.get(f"{base}/jobs/overview", timeout=12)
                if r.ok:
                    data = r.json() or {}
                    jobs = data.get("jobs") or []
                    for j in jobs:
                        jid = j.get("jid") or j.get("id")
                        if not jid:
                            continue
                        st = str(j.get("state", "")).upper()
                        if st in ("RUNNING", "CREATED", "RECONCILING", "RESTARTING", "INITIALIZING"):
                            return str(jid)
            except requests.exceptions.RequestException:
                pass
            time.sleep(1.6)
        return None

    def submit_sql_kubernetes_application(
        self,
        sql: str,
        parallelism: int,
        session_properties: dict,
        k8s_execution_overrides: dict,
        datworks_job_id: int,
    ) -> dict:
        """
        SQL Gateway **v4** deploy script：kubernetes-application 模式整条脚本一次部署。
        依赖 Gateway 暴露 /v4（Flink 2.x）；作业镜像见「系统管理 → 集成」或 FLINK_K8S_APPLICATION_IMAGE。
        返回 {flink_job_id, cluster_id, application_jm_rest, warning}
        """
        statements = self._split_sql_statements(sql)
        if not statements:
            raise RuntimeError("SQL 内容为空或可执行语句数为 0")
        image = self.k8s_application_image()
        if not image:
            raise RuntimeError(
                "未配置 K8s Application 作业镜像。"
                " 请在「系统管理 → 集成」填写 Flink 作业镜像，或设置环境变量 FLINK_K8S_APPLICATION_IMAGE（须与 Session 集群版本一致，例如 apache/flink:2.0.1-java11）。"
            )
        cid_raw = f"dwj-{datworks_job_id}-{int(time.time() * 1000)}"
        cluster_id = re.sub(r"[^a-z0-9\-]", "-", cid_raw.lower()).strip("-")[:63] or f"dwj-{datworks_job_id}"

        parts = [s.strip().rstrip(";") for s in statements]
        script_body = ";\n".join(parts) + ";"

        props = {"execution.runtime-mode": "streaming", "parallelism.default": str(parallelism)}
        props.update(session_properties or {})

        exec_cfg: dict[str, str] = {
            "execution.target": "kubernetes-application",
            "kubernetes.cluster-id": cluster_id,
            "kubernetes.container.image.ref": image,
            "jobmanager.memory.process.size": "1600m",
            "taskmanager.memory.process.size": "1728m",
            "kubernetes.rest-service.exposed.type": "ClusterIP",
        }
        ns = (self.k8s_namespace() or "").strip()
        if ns:
            exec_cfg["kubernetes.namespace"] = ns
        kctx = (settings.FLINK_K8S_CONTEXT or "").strip()
        if kctx:
            exec_cfg["kubernetes.context"] = kctx
        rest_ex = (settings.FLINK_K8S_REST_EXPOSED_TYPE or "").strip()
        if rest_ex:
            exec_cfg["kubernetes.rest-service.exposed.type"] = rest_ex
        for k, v in (k8s_execution_overrides or {}).items():
            if v is None:
                continue
            exec_cfg[str(k)] = v if isinstance(v, str) else str(v)

        session_id: Optional[str] = None
        try:
            try:
                session_resp = self._gw_post("/v4/sessions", json={"properties": props}, timeout=60)
            except RuntimeError as e:
                es = str(e)
                if "404" in es or "405" in es or "Not Found" in es:
                    raise RuntimeError(
                        "当前 SQL Gateway 未提供 v4 REST（/v4/sessions）。K8s Application 脚本部署需要 Flink 2.x 级别 Gateway。"
                    ) from e
                raise
            session_id = session_resp.get("sessionHandle")
            if not session_id:
                raise RuntimeError(f"创建 v4 session 失败: {session_resp}")

            try:
                deploy_resp = self._gw_post(
                    f"/v4/sessions/{session_id}/scripts",
                    json={"script": script_body, "executionConfig": exec_cfg},
                    timeout=300,
                )
            except RuntimeError as e:
                es = str(e)
                if "deploy" in es.lower() or "Failed to deploy" in es or "DeployScript" in es:
                    raise RuntimeError(_application_deploy_error_hint(es)) from e
                raise
            got_cid = str(deploy_resp.get("clusterID") or deploy_resp.get("clusterId") or cluster_id).strip()
            tpl = self.k8s_application_jm_rest_template()
            jm_rest: Optional[str] = None
            warning: Optional[str] = None
            job_id_out: Optional[str] = None
            if tpl:
                jm_rest = tpl.format(cluster_id=got_cid).strip().rstrip("/") if "{cluster_id}" in tpl else tpl.rstrip("/")
            else:
                ns_resolved = self.k8s_namespace_resolved()
                kc_path = (settings.FLINK_K8S_KUBECONFIG_PATH or "").strip()
                if kc_path and os.path.isfile(kc_path):
                    jm_rest = resolve_application_jm_rest_via_nodeport(
                        cluster_id=got_cid,
                        namespace=ns_resolved,
                        kubeconfig_path=kc_path,
                        context=(settings.FLINK_K8S_CONTEXT or "").strip() or None,
                        nodeport_host=(settings.FLINK_K8S_JM_NODEPORT_HOST or "").strip() or "host.docker.internal",
                    )
                if not jm_rest:
                    detail = (
                        "未在集成页或环境变量中配置 JM REST 模板，且未能用 kubeconfig 自动解析 JM NodePort。"
                        if not (kc_path and os.path.isfile(kc_path))
                        else f"已尝试用 kubeconfig 解析 `{got_cid}-rest`（namespace={ns_resolved}）的 NodePort 但未就绪；请确认 kubernetes.rest-service.exposed.type 为 NodePort。"
                    )
                    warning = (
                        f"已提交 Application（clusterID={got_cid}）。{detail} "
                        "可到 Flink/K8s 查看作业，或补全 JM REST 模板 / 检查命名空间与权限。"
                    )

            if jm_rest:
                job_id_out = self._poll_jm_for_running_job(jm_rest, deadline_seconds=150.0)
                if not job_id_out:
                    w2 = (
                        f"已提交 Application（clusterID={got_cid}），但在 {jm_rest} 超时内未探测到运行中 jobId。"
                        " 请核对 JM 地址（模板或 NodePort 主机 FLINK_K8S_JM_NODEPORT_HOST）与 Service 是否就绪。"
                    )
                    warning = f"{warning}\n{w2}" if warning else w2
            return {
                "flink_job_id": job_id_out or "",
                "cluster_id": got_cid,
                "application_jm_rest": jm_rest,
                "warning": warning,
            }
        finally:
            if session_id:
                try:
                    gb = (self._sql_gateway_url or "").strip().rstrip("/")
                    if gb:
                        requests.delete(f"{gb}/v4/sessions/{session_id}", timeout=15)
                except Exception:
                    logger.debug("关闭 v4 SQL Gateway session 失败（可忽略）", exc_info=True)

    def gateway_info_probe(self) -> dict:
        """探测 SQL Gateway /v1/info（供运维页与排障）；不校验 JM。"""
        try:
            base = self.gateway_base_or_raise()
        except RuntimeError as e:
            return {
                "ok": False,
                "configured": False,
                "error": str(e),
            }
        try:
            r = _FLINK_HTTP_GET.get(f"{base}/v1/info", timeout=6)
        except requests.exceptions.RequestException as e:
            return {"ok": False, "configured": True, "base_url": base, "error": _explain_sql_gateway_connect_error(base, e)}
        if r.ok:
            try:
                body = r.json()
            except Exception:
                body = {"raw": r.text[:800]}
            return {"ok": True, "configured": True, "base_url": base, "info": body}
        return {
            "ok": False,
            "configured": True,
            "base_url": base,
            "error": f"HTTP {r.status_code}: {r.text[:800]}",
        }


flink = FlinkClient()


def _flink_client_for_job(db: Session, job: StreamingJob) -> FlinkClient:
    from app.services.ds_runtime import resolve_ds_url_for_backend_http

    cfg = _flink_runtime_cfg_for_job(db, job)
    ui_for_browser = (cfg.flink_ui_url or "").strip() or None
    c = FlinkClient()
    c.apply_runtime(
        resolve_ds_url_for_backend_http(cfg.flink_url) if cfg.flink_url else "",
        resolve_ds_url_for_backend_http(cfg.flink_sql_gateway_url) if cfg.flink_sql_gateway_url else "",
        resolve_ds_url_for_backend_http(cfg.flink_gateway_jobmanager_rest_url)
        if cfg.flink_gateway_jobmanager_rest_url
        else None,
        ui_for_browser,
        k8s_application_image=cfg.flink_k8s_application_image,
        k8s_namespace=cfg.flink_k8s_namespace,
        k8s_application_jm_rest_template=cfg.flink_k8s_application_jm_rest_template,
    )
    return c


def _flink_client_from_runtime_config(cfg: FlinkRuntimeConfig) -> FlinkClient:
    """按合并后的 FlinkRuntimeConfig 构造独立客户端（用于按命名连接探测，避免与全局 flink 串台）。"""
    from app.services.ds_runtime import resolve_ds_url_for_backend_http

    ui_for_browser = (cfg.flink_ui_url or "").strip() or None
    c = FlinkClient()
    c.apply_runtime(
        resolve_ds_url_for_backend_http(cfg.flink_url) if cfg.flink_url else "",
        resolve_ds_url_for_backend_http(cfg.flink_sql_gateway_url) if cfg.flink_sql_gateway_url else "",
        resolve_ds_url_for_backend_http(cfg.flink_gateway_jobmanager_rest_url)
        if cfg.flink_gateway_jobmanager_rest_url
        else None,
        ui_for_browser,
        k8s_application_image=cfg.flink_k8s_application_image,
        k8s_namespace=cfg.flink_k8s_namespace,
        k8s_application_jm_rest_template=cfg.flink_k8s_application_jm_rest_template,
    )
    return c


# ==================== Schema ====================

class JobCopyBody(BaseModel):
    name: Optional[str] = None


class JobCreate(BaseModel):
    workspace_id: int
    name: str
    job_type: str           # SQL / JAR
    script_content: Optional[str] = None
    main_class: Optional[str] = None
    program_args: Optional[str] = None
    parallelism: int = 1
    streaming_properties: Optional[str] = None  # JSON；合并进 Gateway properties；顶级 k8s_application 并入 Application executionConfig
    folder_id: Optional[int] = None
    flink_sql_submit_mode: str = "flink_operator"
    flink_jar_submit_mode: str = "flink_operator"
    flink_session_profile_id: Optional[int] = None
    jar_artifact_id: Optional[int] = None
    jar_version_id: Optional[int] = None
    connector_version_ids: Optional[List[int]] = None
    dependency_file_version_ids: Optional[List[int]] = None
    definition_kind: Optional[str] = None
    pipeline_spec: Optional[Dict[str, Any]] = None

    @field_validator("flink_sql_submit_mode")
    @classmethod
    def _validate_submit_mode_create(cls, v: str) -> str:
        s = (v or _default_sql_submit_mode()).strip().lower()
        if s not in ("session", "kubernetes_application", "flink_operator"):
            raise ValueError("flink_sql_submit_mode 须为 session、kubernetes_application 或 flink_operator")
        return _enforce_sql_submit_mode_allowed(s)

    @field_validator("flink_jar_submit_mode")
    @classmethod
    def _validate_jar_submit_mode_create(cls, v: str) -> str:
        s = (v or _default_jar_submit_mode()).strip().lower()
        if s not in ("session", "flink_operator"):
            raise ValueError("flink_jar_submit_mode 须为 session 或 flink_operator")
        return _enforce_jar_submit_mode_allowed(s)


class JobUpdate(BaseModel):
    name: Optional[str] = None
    script_content: Optional[str] = None
    main_class: Optional[str] = None
    program_args: Optional[str] = None
    parallelism: Optional[int] = None
    streaming_properties: Optional[str] = None
    folder_id: Optional[int] = None
    flink_sql_submit_mode: Optional[str] = None
    flink_jar_submit_mode: Optional[str] = None
    flink_session_profile_id: Optional[int] = None
    jar_artifact_id: Optional[int] = None
    jar_version_id: Optional[int] = None
    connector_version_ids: Optional[List[int]] = None
    dependency_file_version_ids: Optional[List[int]] = None
    definition_kind: Optional[str] = None
    pipeline_spec: Optional[Dict[str, Any]] = None

    @field_validator("flink_sql_submit_mode")
    @classmethod
    def _validate_submit_mode_update(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in ("session", "kubernetes_application", "flink_operator"):
            raise ValueError("flink_sql_submit_mode 须为 session、kubernetes_application 或 flink_operator")
        return _enforce_sql_submit_mode_allowed(s)

    @field_validator("flink_jar_submit_mode")
    @classmethod
    def _validate_jar_submit_mode_update(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().lower()
        if s not in ("session", "flink_operator"):
            raise ValueError("flink_jar_submit_mode 须为 session 或 flink_operator")
        return _enforce_jar_submit_mode_allowed(s)


class SubmitJobBody(BaseModel):
    """POST /streaming/jobs/{id}/submit：大段 SQL 请放 body，勿用 query（易超长或被代理截断）。"""
    script_content: Optional[str] = None


class ReleaseSubmitBody(BaseModel):
    """创建发布快照，不触发 Flink 部署，也不覆盖编辑中的草稿。"""

    script_content: Optional[str] = None
    release_note: Optional[str] = Field(default=None, max_length=4000)


class StreamingReleaseResponse(BaseModel):
    id: int
    job_id: int
    version: int
    job_name: str
    job_type: str
    script_content: Optional[str] = None
    jar_path: Optional[str] = None
    main_class: Optional[str] = None
    program_args: Optional[str] = None
    parallelism: int
    streaming_properties: Optional[str] = None
    flink_sql_submit_mode: Optional[str] = None
    flink_jar_submit_mode: Optional[str] = None
    flink_session_profile_id: Optional[int] = None
    jar_artifact_id: Optional[int] = None
    jar_version_id: Optional[int] = None
    connector_version_ids: List[int] = Field(default_factory=list)
    dependency_file_version_ids: List[int] = Field(default_factory=list)
    definition_kind: str = "sql"
    pipeline_spec: Optional[Dict[str, Any]] = None
    compiler_version: Optional[str] = None
    generated_artifact: Optional[Dict[str, Any]] = None
    spec_hash: Optional[str] = None
    content_hash: str
    release_note: Optional[str] = None
    approval_status: str
    submitted_by: Optional[int] = None
    submitted_by_username: Optional[str] = None
    submitted_at: datetime
    approved_by: Optional[int] = None
    approved_by_username: Optional[str] = None
    approved_at: Optional[datetime] = None
    approval_comment: Optional[str] = None


class StreamingRestorePointResponse(BaseModel):
    id: int
    job_id: int
    release_id: Optional[int] = None
    operation_id: Optional[int] = None
    point_type: str
    status: str
    path: Optional[str] = None
    flink_job_id: Optional[str] = None
    trigger_reason: Optional[str] = None
    metadata_json: Optional[str] = None
    error_message: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class StreamingOperationResponse(BaseModel):
    id: int
    job_id: int
    release_id: Optional[int] = None
    restore_point_id: Optional[int] = None
    operation_type: str
    status: str
    idempotency_key: Optional[str] = None
    requested_by: Optional[int] = None
    requested_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    flink_job_id: Optional[str] = None
    flink_deployment_name: Optional[str] = None
    restore_path: Optional[str] = None
    request_json: Optional[str] = None
    result_json: Optional[str] = None
    error_message: Optional[str] = None


class StreamingDeployBody(BaseModel):
    release_id: Optional[int] = None
    parallelism: Optional[int] = Field(default=None, ge=1)
    streaming_properties: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class StreamingRestartBody(StreamingDeployBody):
    restore_mode: str = "latest"
    restore_point_id: Optional[int] = None
    allow_non_restored_state: bool = False
    confirm_stateless: bool = False

    @field_validator("restore_mode")
    @classmethod
    def _validate_restore_mode(cls, value: str) -> str:
        mode = (value or "latest").strip().lower()
        if mode not in ("latest", "specific", "stateless"):
            raise ValueError("restore_mode 须为 latest、specific 或 stateless")
        return mode


class StreamingStopBody(BaseModel):
    mode: str = "savepoint"
    timeout_seconds: float = Field(default=120.0, ge=5.0, le=900.0)
    # False（默认）：触发挂起后立即返回，Savepoint 在后台完成，避免运维页弹窗卡死
    # True：同步等到完成/失败（单测与脚本）
    wait: bool = False
    idempotency_key: Optional[str] = Field(default=None, max_length=128)

    @field_validator("mode")
    @classmethod
    def _validate_stop_mode(cls, value: str) -> str:
        mode = (value or "savepoint").strip().lower()
        if mode != "savepoint":
            raise ValueError("默认停止仅支持 savepoint；强制删除请使用 cancel")
        return mode


class StreamPreviewSqlIn(BaseModel):
    workspace_id: int
    sql: str
    limit: int = Field(default=100, ge=1, le=10000)


def _jar_session_blocked_reason() -> Optional[str]:
    """Operator 与 Session Flink 主版本不一致时，阻止 Session JAR 误提交。"""
    op_ver = (settings.FLINK_OPERATOR_FLINK_VERSION or "").strip().lower()
    app_img = (settings.FLINK_K8S_APPLICATION_IMAGE or "").strip().lower()
    if not op_ver or not app_img:
        return None
    op_major = op_ver.replace("v", "").split("_")[0] if "_" in op_ver else ""
    session_major = "2" if "2.0" in app_img or "2.1" in app_img or "2.2" in app_img else "1"
    if op_ver.startswith("v2_") and session_major == "2":
        return None
    if op_ver.startswith("v1_") and session_major == "1":
        return None
    if not (settings.FLINK_URL or "").strip():
        return None
    return (
        f"当前 Operator flinkVersion={op_ver} 与 Session 镜像 {settings.FLINK_K8S_APPLICATION_IMAGE} 主版本不一致，"
        "Session JAR 提交易类加载失败。请统一 Flink 版本，或改用「Flink Operator 生产」提交。"
    )


def _require_flink_profile_in_workspace(db: Session, workspace_id: int, profile_id: Optional[int]) -> None:
    if profile_id is None:
        return
    p = (
        db.query(FlinkSessionProfile)
        .filter(
            FlinkSessionProfile.id == int(profile_id),
            FlinkSessionProfile.workspace_id == int(workspace_id),
        )
        .first()
    )
    if not p:
        raise HTTPException(status_code=400, detail="Flink Session 配置不存在或不属于当前工作空间")


def _resolve_flink_infra_probe_client(
    db: Session,
    current_user: User,
    workspace_id: Optional[int],
    flink_session_profile_id: Optional[int],
) -> Tuple[FlinkClient, FlinkRuntimeConfig, dict]:
    """
    集群与健康 / 连通性自检用的 Flink 客户端。
    未指定 flink_session_profile_id 时沿用全局 flink（与 Depends 刷新后的平台默认一致）；
    指定时构造独立客户端，避免与全局单例并发串台。
    """
    assert_gido_stream_infra_probe_access(db, current_user)
    probe: dict = {"kind": "platform_default"}
    if flink_session_profile_id is None:
        cfg = get_flink_runtime(db)
        return flink, cfg, probe
    if workspace_id is None:
        raise HTTPException(
            status_code=400,
            detail="探测工作空间「Flink 集群连接」时须同时传入 workspace_id 与 flink_session_profile_id",
        )
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    _require_flink_profile_in_workspace(db, workspace_id, flink_session_profile_id)
    p = (
        db.query(FlinkSessionProfile)
        .filter(
            FlinkSessionProfile.id == int(flink_session_profile_id),
            FlinkSessionProfile.workspace_id == int(workspace_id),
        )
        .first()
    )
    assert p is not None
    cfg = get_flink_runtime_for_workspace_profile(db, workspace_id, flink_session_profile_id)
    fc = _flink_client_from_runtime_config(cfg)
    probe = {
        "kind": "flink_session_profile",
        "workspace_id": int(workspace_id),
        "profile_id": int(flink_session_profile_id),
        "profile_name": ((p.name or "").strip() or f"#{flink_session_profile_id}"),
    }
    return fc, cfg, probe


def _streaming_connectivity_payload(fc: FlinkClient, cfg: FlinkRuntimeConfig, probe: dict) -> dict:
    """GIDO ↔ JobManager ↔ SQL Gateway 连通性（排障），始终返回 dict（HTTP 200）。"""
    from app.services.ds_runtime import resolve_ds_url_for_backend_http

    jm_configured = (cfg.flink_url or "").strip() or None
    jm_resolved = resolve_ds_url_for_backend_http(jm_configured) if jm_configured else ""
    jm_effective = (fc.jm_base or "").strip() or jm_resolved
    in_docker = os.path.exists("/.dockerenv")
    localhost_rewritten = bool(
        jm_configured
        and jm_effective
        and jm_configured != jm_effective
        and "host.docker.internal" in jm_effective.lower()
    )

    jm: dict = {"ok": False, "overview": None}
    try:
        jm["overview"] = fc.overview()
        jm["ok"] = True
    except Exception as e:
        jm["error"] = str(e)
    gw = fc.gateway_info_probe()
    gw_target: dict = {}
    raw_gwjm = (fc._gateway_jm_rest_url or fc.jm_base or "").strip()
    try:
        u = urlparse(raw_gwjm if raw_gwjm else "http://invalid")
        if u.hostname:
            port = u.port
            if port is None:
                port = 443 if (u.scheme or "http") == "https" else 8081
            gw_target = {"rest.address": u.hostname, "rest.port": port}
    except Exception:
        gw_target = {"parse_error": True}

    hints: List[str] = []
    if probe.get("kind") == "flink_session_profile":
        pn = probe.get("profile_name") or ""
        pid = probe.get("profile_id")
        hints.append(
            f"当前探测目标：本工作空间 Flink 集群连接「{pn}」（id={pid}）。"
            "以下为该命名连接与平台默认合并后的连通性；切换页面顶部「探测目标」可对比其他连接。"
        )
    if not gw.get("configured"):
        hints.append(
            "未配置 SQL Gateway：在「系统管理 → 集成」填写 Gateway REST，或在 backend .env / 部署环境设 FLINK_SQL_GATEWAY_URL。"
            " 须为 Gateway 的 /v1 根（常见端口 8083），不要用 JobManager 的 :8081。"
            " Kubernetes Session 见仓库根 k8s/legacy/flink.yaml，对外常配合 Ingress 或 NodePort。"
        )
    elif not gw.get("ok"):
        hints.append(
            "SQL Gateway 不可达：先确认 Gateway 已启动，后端能访问配置的地址（可对照下方错误、`curl …/v1/info`）。"
            " 若 URL 含 host.docker.internal 且出现 Network unreachable：在 Linux 宿主机跑后端时请改用 127.0.0.1 或 Ingress 映射端口。"
        )
    if not jm["ok"]:
        eff = jm_effective or "(未配置 FLINK_URL / 库内 Flink 地址)"
        if probe.get("kind") == "flink_session_profile":
            hints.append(
                f"JobManager REST 探测失败：后端实际请求的是「{eff}」。"
                "「configured_url」为当前命名连接与平台默认合并后的基址（环境 / 系统 Flink 集成 + 连接中非空覆写项）。"
            )
        else:
            hints.append(
                f"JobManager REST 探测失败：后端实际请求的是「{eff}」。"
                "「configured_url」为合并 .env / 环境变量与 系统管理 → 集成与连接 → Flink 后的基址；若库内有覆盖，以库内为准。"
            )
        if isinstance(eff, str) and (eff.startswith("http://") or eff.startswith("https://")):
            safe = eff.replace("'", "%27")
            hints.append(f"在后端所在机器上自检（应返回 JSON）：curl -sS -m 8 '{safe}/overview'")
        hints.append(
            "常见原因：① Flink Session 未部署或端口不对（推荐 kubectl apply -f k8s/legacy/flink.yaml）；"
            "② 后端在 Docker 内且配置了 127.0.0.1——会自动改为 host.docker.internal，Linux 需在 compose 增加 extra_hosts: "
            "\"host.docker.internal:host-gateway\"；③ 跨网络时请在集成页填写 JM 可被后端访问的 URL。"
        )
    elif jm.get("ok") and gw.get("ok") is False:
        hints.append(
            "若 JobManager 已通但 SQL 提交仍失败：Gateway 进程必须能连上 executionConfig 里的 JobManager REST。"
            " 跨 Docker 网络或 hostname 解析不一致时，设置 FLINK_GATEWAY_JOBMANAGER_REST_URL（compose 可用 GIDO_FLINK_GATEWAY_JOBMANAGER_REST_URL），"
            " 使 Gateway 容器内能解析并访问该地址。"
        )
    if not (cfg.flink_k8s_application_image or "").strip():
        hints.append(
            "若使用「K8s Application」提交模式：请配置 Flink 作业镜像（「系统管理 → 集成」或 FLINK_K8S_APPLICATION_IMAGE）；"
            " 建议同时配置 JM REST 模板（含 {cluster_id}）以便回填 jobId、状态与取消。"
            " 需 SQL Gateway 支持 v4 REST（/v4/sessions），一般为 Flink 2.x。"
        )

    return {
        "probe": probe,
        "jobmanager": {
            "url": jm_effective,
            "configured_url": jm_configured,
            "effective_url": jm_effective,
            "running_in_docker": in_docker,
            "localhost_rewritten_for_docker": localhost_rewritten,
            "ok": jm["ok"],
            "overview": jm.get("overview"),
            "error": jm.get("error"),
        },
        "sql_gateway": gw,
        "gateway_jm_execution_target": gw_target,
        "flink_ui_base": flink_ui_base_from_runtime_cfg(cfg) or None,
        "hints": hints,
    }


# ==================== 接口 ====================

@router.get(
    "/overview",
    dependencies=[Depends(_refresh_flink_from_db)],
)
def flink_overview(
    workspace_id: Optional[int] = Query(None, description="探测命名连接时必填，与 flink_session_profile_id 成对出现"),
    flink_session_profile_id: Optional[int] = Query(
        None, description="工作空间 Flink 集群连接 id；不传则探测平台默认（全局 flink）"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Flink 集群概览（可选按工作空间命名连接探测，与作业绑定配置一致）。"""
    fc, _cfg, _probe = _resolve_flink_infra_probe_client(db, current_user, workspace_id, flink_session_profile_id)
    try:
        return fc.overview()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Flink 连接失败: {e}")


@router.get(
    "/connectivity",
    dependencies=[Depends(_refresh_flink_from_db)],
)
def streaming_connectivity(
    workspace_id: Optional[int] = Query(None, description="探测命名连接时必填"),
    flink_session_profile_id: Optional[int] = Query(None, description="不传则探测平台默认 Flink"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """GIDO ↔ JobManager ↔ SQL Gateway 连通性（排障），始终 200。可选按命名连接探测。"""
    fc, cfg, probe = _resolve_flink_infra_probe_client(db, current_user, workspace_id, flink_session_profile_id)
    return _streaming_connectivity_payload(fc, cfg, probe)


class FlinkSessionProfileCreate(BaseModel):
    workspace_id: int
    name: str
    flink_url: Optional[str] = None
    flink_sql_gateway_url: Optional[str] = None
    flink_gateway_jobmanager_rest_url: Optional[str] = None
    flink_ui_url: Optional[str] = None
    flink_k8s_application_image: Optional[str] = None
    flink_k8s_namespace: Optional[str] = None
    flink_k8s_application_jm_rest_template: Optional[str] = None
    flink_k8s_cluster_domain: Optional[str] = None
    flink_k8s_apiserver_fallback_url: Optional[str] = None
    flink_k8s_jm_rpc_host: Optional[str] = None
    flink_k8s_sql_gateway_rest_host: Optional[str] = None


class FlinkSessionProfileUpdate(BaseModel):
    name: Optional[str] = None
    flink_url: Optional[str] = None
    flink_sql_gateway_url: Optional[str] = None
    flink_gateway_jobmanager_rest_url: Optional[str] = None
    flink_ui_url: Optional[str] = None
    flink_k8s_application_image: Optional[str] = None
    flink_k8s_namespace: Optional[str] = None
    flink_k8s_application_jm_rest_template: Optional[str] = None
    flink_k8s_cluster_domain: Optional[str] = None
    flink_k8s_apiserver_fallback_url: Optional[str] = None
    flink_k8s_jm_rpc_host: Optional[str] = None
    flink_k8s_sql_gateway_rest_host: Optional[str] = None


_FLINK_PROFILE_OVERRIDE_FIELDS = (
    "flink_url",
    "flink_sql_gateway_url",
    "flink_gateway_jobmanager_rest_url",
    "flink_ui_url",
    "flink_k8s_application_image",
    "flink_k8s_namespace",
    "flink_k8s_application_jm_rest_template",
    "flink_k8s_cluster_domain",
    "flink_k8s_apiserver_fallback_url",
    "flink_k8s_jm_rpc_host",
    "flink_k8s_sql_gateway_rest_host",
)


def _dict_has_flink_connector_override(d: dict) -> bool:
    """工作空间「集群连接」须至少有一项非空，否则与平台默认完全等价，无需单独建连接。"""
    for k in _FLINK_PROFILE_OVERRIDE_FIELDS:
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str) and v.strip():
            return True
    return False


def _flink_runtime_config_public(cfg: FlinkRuntimeConfig) -> dict:
    return {
        "flink_url": cfg.flink_url or None,
        "flink_sql_gateway_url": cfg.flink_sql_gateway_url or None,
        "flink_gateway_jobmanager_rest_url": cfg.flink_gateway_jobmanager_rest_url or None,
        "flink_ui_url": cfg.flink_ui_url or None,
        "flink_k8s_application_image": cfg.flink_k8s_application_image or None,
        "flink_k8s_namespace": cfg.flink_k8s_namespace or None,
        "flink_k8s_application_jm_rest_template": cfg.flink_k8s_application_jm_rest_template or None,
        "flink_k8s_cluster_domain": cfg.flink_k8s_cluster_domain or None,
        "flink_k8s_apiserver_fallback_url": cfg.flink_k8s_apiserver_fallback_url or None,
        "flink_k8s_jm_rpc_host": cfg.flink_k8s_jm_rpc_host or None,
        "flink_k8s_sql_gateway_rest_host": cfg.flink_k8s_sql_gateway_rest_host or None,
    }


def _flink_session_profile_public(p: FlinkSessionProfile) -> dict:
    return {
        "id": p.id,
        "workspace_id": p.workspace_id,
        "name": p.name,
        "flink_url": p.flink_url,
        "flink_sql_gateway_url": p.flink_sql_gateway_url,
        "flink_gateway_jobmanager_rest_url": p.flink_gateway_jobmanager_rest_url,
        "flink_ui_url": p.flink_ui_url,
        "flink_k8s_application_image": p.flink_k8s_application_image,
        "flink_k8s_namespace": p.flink_k8s_namespace,
        "flink_k8s_application_jm_rest_template": p.flink_k8s_application_jm_rest_template,
        "flink_k8s_cluster_domain": p.flink_k8s_cluster_domain,
        "flink_k8s_apiserver_fallback_url": p.flink_k8s_apiserver_fallback_url,
        "flink_k8s_jm_rpc_host": p.flink_k8s_jm_rpc_host,
        "flink_k8s_sql_gateway_rest_host": p.flink_k8s_sql_gateway_rest_host,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
        "created_by": p.created_by,
    }


@router.get("/flink-runtime")
def flink_runtime_info(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """只读：统一 Flink 运行时（镜像内连接器、默认 Paimon warehouse、Operator 命名空间）。"""
    from app.services.flink_runtime_catalog import flink_runtime_api_payload

    assert_gido_stream_infra_probe_access(db, current_user)
    return flink_runtime_api_payload()


@router.post("/preview-sql")
def stream_preview_sql(
    body: StreamPreviewSqlIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stream SQL 预览：在 Flink 运行时镜像中 batch 执行 SELECT，返回结果表（不创建 FlinkDeployment）。"""
    from app.services.stream_sql_preview import run_stream_sql_preview

    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_READ)
    sql = substitute_stream_preview_sql(db, body.workspace_id, body.sql)
    return run_stream_sql_preview(sql, limit=body.limit)


def substitute_stream_preview_sql(db: Session, workspace_id: int, sql: str) -> str:
    from app.services.workspace_variables import substitute_script_variables

    return substitute_script_variables(db, int(workspace_id), sql or "", "stream")


@router.get("/operator-overview")
def operator_overview(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Flink Kubernetes Operator 运维概览：运行时配置、FlinkDeployment 列表与 JM/TM 健康摘要。"""
    from app.services.flink_operator_submit import operator_overview_payload

    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    payload = operator_overview_payload(workspace_id=workspace_id)
    jobs = (
        db.query(StreamingJob)
        .filter(StreamingJob.workspace_id == workspace_id)
        .all()
    )
    jobs_by_id = {str(j.id): j for j in jobs}
    for dep in payload.get("deployments") or []:
        jid = str(dep.get("job_id") or "")
        job = jobs_by_id.get(jid)
        if job:
            dep["job_name"] = getattr(job, "name", None)
            dep["job_status"] = getattr(job, "status", None)
    with_dep = [
        j for j in jobs
        if (getattr(j, "flink_operator_deployment_name", None) or "").strip()
    ]
    payload["summary"]["jobs_total"] = len(jobs)
    payload["summary"]["jobs_with_deployment"] = len(with_dep)
    payload["summary"]["jobs_running"] = sum(1 for j in jobs if (j.status or "").lower() == "running")
    payload["summary"]["jobs_failed"] = sum(1 for j in jobs if (j.status or "").lower() == "failed")
    return payload


@router.get("/flink-platform-defaults")
def flink_platform_defaults_for_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    只读：当前「平台默认」合并结果（环境变量 + 系统管理 → 集成），供工作空间「集群连接」页对照。
    与成熟产品一致：平台层为租户默认；工作空间连接仅在非空字段上覆写，不形成两套独立真相源。
    """
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    cfg = get_flink_runtime(db)
    return {
        "merge_layers": [
            "① 环境变量 / 部署配置（FLINK_*、FLINK_K8S_*）",
            "② 平台集成（系统管理 → 集成，单行库表覆盖）",
            "③ 工作空间集群连接（本页命名连接：仅填写与上两层不同的字段）",
        ],
        "job_rule": "作业未选择命名连接时，仅使用 ①②；选择后使用 ①②③（③ 中非空字段覆盖同名项）。",
        "effective": _flink_runtime_config_public(cfg),
    }


@router.get("/flink-session-profiles")
def list_flink_session_profiles(
    workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    rows = (
        db.query(FlinkSessionProfile)
        .filter(FlinkSessionProfile.workspace_id == workspace_id)
        .order_by(FlinkSessionProfile.id.asc())
        .all()
    )
    return [_flink_session_profile_public(p) for p in rows]


@router.post("/flink-session-profiles")
def create_flink_session_profile(
    body: FlinkSessionProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    nm = (body.name or "").strip()
    if not nm:
        raise HTTPException(status_code=400, detail="名称不能为空")
    data = body.model_dump(exclude={"workspace_id", "name"})
    if not _dict_has_flink_connector_override(data):
        raise HTTPException(
            status_code=400,
            detail=(
                "请至少填写一项与「平台默认」不同的 Flink 地址或 K8s 项；留空的字段会继续继承平台集成。"
                "若与平台完全一致，无需创建命名连接，作业在开发页选择「默认（平台）」即可。"
            ),
        )
    p = FlinkSessionProfile(workspace_id=body.workspace_id, name=nm, created_by=current_user.id, **data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _flink_session_profile_public(p)


@router.put("/flink-session-profiles/{profile_id}")
def update_flink_session_profile(
    profile_id: int,
    body: FlinkSessionProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = db.query(FlinkSessionProfile).filter(FlinkSessionProfile.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="配置不存在")
    assert_workspace_data_capability(db, current_user, p.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        n = str(patch["name"]).strip()
        if not n:
            raise HTTPException(status_code=400, detail="名称不能为空")
        patch["name"] = n
    merged_ov = {k: getattr(p, k, None) for k in _FLINK_PROFILE_OVERRIDE_FIELDS}
    for k in _FLINK_PROFILE_OVERRIDE_FIELDS:
        if k in patch:
            merged_ov[k] = patch[k]
    if not _dict_has_flink_connector_override(merged_ov):
        raise HTTPException(
            status_code=400,
            detail="保存后该连接已无任何覆盖项，等同于平台默认；请至少保留一项非空地址/K8s 项，或删除本连接并在作业上使用「默认（平台）」。",
        )
    for k, v in patch.items():
        setattr(p, k, v)
    p.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(p)
    return _flink_session_profile_public(p)


@router.delete("/flink-session-profiles/{profile_id}")
def delete_flink_session_profile(
    profile_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    p = db.query(FlinkSessionProfile).filter(FlinkSessionProfile.id == profile_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="配置不存在")
    assert_workspace_data_capability(db, current_user, p.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    n = db.query(StreamingJob).filter(StreamingJob.flink_session_profile_id == profile_id).count()
    if n:
        raise HTTPException(status_code=409, detail=f"仍有 {n} 个实时作业绑定该配置，请先解除绑定后再删除")
    db.delete(p)
    db.commit()
    return {"message": "已删除"}


def _operator_deployment_name_for_job(job: StreamingJob) -> Optional[str]:
    from app.services.flink_operator_submit import deployment_name_for_job, sql_deployment_name_for_job

    dep = (getattr(job, "flink_operator_deployment_name", None) or "").strip()
    ws = int(getattr(job, "workspace_id", None) or 0)
    jt = (job.job_type or "").upper()
    if jt == "JAR":
        if _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None)) != "flink_operator":
            return None
        return dep or deployment_name_for_job(job.id, ws)
    if jt == "SQL":
        if _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)) != "flink_operator":
            return None
        return dep or sql_deployment_name_for_job(job.id, ws)
    return None


def _record_submit_history_version(
    db: Session, job: StreamingJob, user_id: int
) -> Tuple[Optional[int], Optional[str]]:
    """提交前写入历史快照，返回 (history_id, sql_hash)。"""
    from app.services.gido_deployment_meta import sql_script_hash

    _append_streaming_job_history_snapshot(db, job, user_id)
    db.flush()
    hist = (
        db.query(StreamingJobHistory)
        .filter(StreamingJobHistory.job_id == job.id)
        .order_by(StreamingJobHistory.id.desc())
        .first()
    )
    version_id = int(hist.id) if hist else None
    content = (job.script_content or "") if (job.job_type or "").upper() == "SQL" else ""
    digest = sql_script_hash(content) if content else None
    return version_id, digest


def _build_operator_deployment_meta(
    job: StreamingJob,
    *,
    user: User,
    sql_version: Optional[int] = None,
    sql_hash: Optional[str] = None,
) -> "GidoDeploymentMeta":
    from app.services.gido_deployment_meta import GidoDeploymentMeta, utc_now_iso

    jt = "sql" if (job.job_type or "").upper() == "SQL" else "jar"
    return GidoDeploymentMeta(
        workspace_id=int(getattr(job, "workspace_id", None) or 0),
        job_id=int(job.id),
        job_type=jt,
        job_name=getattr(job, "name", None),
        sql_version=str(sql_version) if sql_version is not None else None,
        sql_hash=sql_hash,
        submitted_by=(getattr(user, "username", None) or str(user.id)),
        submitted_at=utc_now_iso(),
    )


def _jm_base_for_job(
    job: StreamingJob,
    *,
    deadline_seconds: float = 3.0,
    prefer_stored: bool = False,
) -> Optional[str]:
    """Flink JM REST 基址；Operator 本机开发时运行时解析隧道/NodePort，不用 DB 内集群 DNS。"""
    stored = (getattr(job, "flink_application_jm_rest", None) or "").strip() or None
    if prefer_stored and stored:
        return stored
    dep = _operator_deployment_name_for_job(job)
    if dep:
        from app.services.flink_operator_submit import _operator_namespace, effective_operator_jm_rest

        resolved = effective_operator_jm_rest(
            job.id,
            dep,
            _operator_namespace(),
            stored,
            deadline_seconds=deadline_seconds,
        )
        if resolved:
            return resolved
    return stored


def _release_operator_ui_tunnel(job: StreamingJob) -> None:
    dep = _operator_deployment_name_for_job(job)
    if not dep:
        return
    from app.services.flink_operator_ui_tunnel import release_ui_tunnel
    from app.services.flink_operator_submit import _operator_namespace

    release_ui_tunnel(dep, _operator_namespace())


def _job_needs_live_status_sync(job: StreamingJob) -> bool:
    """运维页对账：有 Operator deployment / JobId 则按集群回填 GIDO（含库内 cancelled，用于纠正误标）。"""
    st = (job.status or "").strip().lower()
    if _operator_deployment_name_for_job(job):
        return True
    if job.flink_job_id:
        return True
    if st in ("cancelled", "finished", "failed"):
        return False
    cid = getattr(job, "flink_application_cluster_id", None)
    if cid:
        return True
    return False


def _mark_job_stopped(db: Session, job: StreamingJob, *, clear_runtime: bool = True) -> None:
    job.status = "cancelled"
    if clear_runtime:
        job.flink_job_id = None
        job.flink_application_jm_rest = None
    job.updated_at = datetime.utcnow()
    db.commit()


def _require_stream_folder(db: Session, workspace_id: int, folder_id: Optional[int]) -> Optional[NodeFolder]:
    if folder_id is None:
        return None
    fo = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not fo or fo.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="目标文件夹不存在或不属于该工作空间")
    if (getattr(fo, "scope", None) or "batch") != "stream":
        raise HTTPException(status_code=400, detail="目标文件夹不属于实时作业目录树")
    return fo


def _next_stream_job_sort_order(db: Session, workspace_id: int, folder_id: Optional[int]) -> int:
    q = db.query(func.max(StreamingJob.sort_order)).filter(StreamingJob.workspace_id == workspace_id)
    if folder_id is None:
        q = q.filter(StreamingJob.folder_id.is_(None))
    else:
        q = q.filter(StreamingJob.folder_id == folder_id)
    mx = q.scalar()
    return (int(mx) if mx is not None else 0) + 10


class StreamFolderCreate(BaseModel):
    workspace_id: int
    name: str
    parent_id: Optional[int] = None


class StreamFolderRename(BaseModel):
    name: str


class StreamJobFolderPatch(BaseModel):
    folder_id: Optional[int] = None


class StreamJobReorderIn(BaseModel):
    workspace_id: int
    folder_id: Optional[int] = None
    job_ids: List[int]


@router.get("/folders")
def list_stream_folders(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    folders = (
        db.query(NodeFolder)
        .filter(NodeFolder.workspace_id == workspace_id, NodeFolder.scope == "stream")
        .all()
    )
    return [{"id": f.id, "name": f.name, "parent_id": f.parent_id, "scope": "stream"} for f in folders]


@router.post("/folders")
def create_stream_folder(
    body: StreamFolderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    if body.parent_id is not None:
        _require_stream_folder(db, body.workspace_id, body.parent_id)
    folder = NodeFolder(workspace_id=body.workspace_id, name=name, parent_id=body.parent_id, scope="stream")
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "scope": "stream"}


@router.put("/folders/{folder_id}")
def rename_stream_folder(
    folder_id: int,
    body: StreamFolderRename,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder or (getattr(folder, "scope", None) or "batch") != "stream":
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    folder.name = name
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "scope": "stream"}


@router.delete("/folders/{folder_id}")
def delete_stream_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder or (getattr(folder, "scope", None) or "batch") != "stream":
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    kids = db.query(NodeFolder).filter(NodeFolder.parent_id == folder_id).count()
    if kids:
        raise HTTPException(status_code=400, detail="请先删除子文件夹")
    db.query(StreamingJob).filter(StreamingJob.folder_id == folder_id).update({"folder_id": None})
    db.delete(folder)
    db.commit()
    return {"message": "删除成功"}


@router.patch("/jobs/{job_id}/folder")
def move_stream_job_folder(
    job_id: int,
    body: StreamJobFolderPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    folder_id = body.folder_id
    _require_stream_folder(db, int(job.workspace_id), folder_id)
    job.folder_id = folder_id
    job.sort_order = _next_stream_job_sort_order(db, int(job.workspace_id), folder_id)
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return _streaming_job_public_dict(db, job)


@router.put("/jobs/reorder")
def reorder_stream_jobs(
    body: StreamJobReorderIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    if not body.job_ids:
        return {"ok": True}
    _require_stream_folder(db, body.workspace_id, body.folder_id)
    jobs = (
        db.query(StreamingJob)
        .filter(StreamingJob.workspace_id == body.workspace_id, StreamingJob.id.in_(body.job_ids))
        .all()
    )
    if len(jobs) != len(set(body.job_ids)):
        raise HTTPException(status_code=400, detail="存在无效作业 ID")
    for j in jobs:
        jf = j.folder_id if j.folder_id is not None else None
        if jf != body.folder_id:
            raise HTTPException(status_code=400, detail="作业与目标目录不一致，请先移动到同一目录")
    for i, jid in enumerate(body.job_ids):
        job = next(j for j in jobs if j.id == jid)
        job.sort_order = (i + 1) * 10
    db.commit()
    return {"ok": True}


@router.get("/jobs")
def list_jobs(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """列出工作空间下的实时任务"""
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    jobs = (
        db.query(StreamingJob)
        .filter(StreamingJob.workspace_id == workspace_id)
        .order_by(StreamingJob.sort_order.asc(), StreamingJob.id.asc())
        .all()
    )
    # 列表只读库（对齐批处理数据开发）：集群/JM 状态由「作业运维」jobs-status-sync 负责
    uids: List[Optional[int]] = []
    for j in jobs:
        uids.append(getattr(j, "owner_id", None) or j.created_by)
        uids.append(getattr(j, "last_submitted_by", None))
    umap = _username_map(db, uids)
    base = get_flink_runtime(db)
    pids = {getattr(j, "flink_session_profile_id", None) for j in jobs}
    pids.discard(None)
    prof_by_id: dict = {}
    if pids:
        for p in db.query(FlinkSessionProfile).filter(FlinkSessionProfile.id.in_(list(pids))).all():
            prof_by_id[p.id] = p
    cfg_by_job: dict = {}
    for j in jobs:
        pid = getattr(j, "flink_session_profile_id", None)
        if not pid:
            cfg_by_job[j.id] = base
            continue
        pr = prof_by_id.get(int(pid))
        cfg_by_job[j.id] = (
            apply_flink_row_overrides(base, pr) if pr and int(pr.workspace_id) == int(j.workspace_id) else base
        )

    def _pname(jj: StreamingJob) -> Optional[str]:
        x = getattr(jj, "flink_session_profile_id", None)
        if not x:
            return None
        pr = prof_by_id.get(int(x))
        return pr.name if pr and int(pr.workspace_id) == int(jj.workspace_id) else None

    return [
        _streaming_job_public_dict(
            db, j, username_by_id=umap, runtime_cfg=cfg_by_job[j.id], profile_name=_pname(j)
        )
        for j in jobs
    ]


@router.post("/jobs")
def create_job(job_in: JobCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, job_in.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    _require_flink_profile_in_workspace(db, job_in.workspace_id, job_in.flink_session_profile_id)
    data = job_in.model_dump()
    data["definition_kind"] = data.get("definition_kind") or (
        "jar" if data.get("job_type") == "JAR" else "sql"
    )
    if data["definition_kind"] not in ("sql", "jar", "pipeline"):
        raise HTTPException(status_code=422, detail="definition_kind 须为 sql、jar 或 pipeline")
    if data.get("definition_kind") == "pipeline":
        if not data.get("pipeline_spec"):
            raise HTTPException(status_code=422, detail="pipeline 作业必须提供 pipeline_spec")
        from app.api.stream_pipeline import _preflight_with_runtime
        from app.services.stream_pipeline_compiler import compile_pipeline
        from app.services.stream_pipeline_spec import PipelineSpec

        parsed_spec = PipelineSpec.model_validate(data["pipeline_spec"])
        check = _preflight_with_runtime(
            db, parsed_spec, workspace_id=int(job_in.workspace_id)
        )
        if not check["ok"]:
            raise HTTPException(status_code=422, detail=check)
        data["pipeline_spec"] = parsed_spec.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        artifact = compile_pipeline(parsed_spec)
        data["compiler_version"] = artifact["compiler_version"]
        data["generated_artifact"] = artifact
        data["spec_hash"] = artifact["spec_hash"]
        data["script_content"] = artifact["sql"]
    data["name"] = normalize_stream_job_name(data.get("name") or "")
    ensure_stream_job_name_available(db, int(job_in.workspace_id), data["name"])
    folder_id = data.get("folder_id")
    _require_stream_folder(db, int(job_in.workspace_id), folder_id)
    data["sort_order"] = _next_stream_job_sort_order(db, int(job_in.workspace_id), folder_id)
    if "connector_version_ids" in data:
        data["connector_version_ids"] = _dump_version_id_list(data.get("connector_version_ids"))
    if "dependency_file_version_ids" in data:
        data["dependency_file_version_ids"] = _dump_version_id_list(data.get("dependency_file_version_ids"))
    job = StreamingJob(**data, created_by=current_user.id, owner_id=current_user.id, is_locked=False)
    db.add(job)
    db.commit()
    db.refresh(job)
    return _streaming_job_public_dict(db, job)


def _unique_streaming_job_copy_name(db: Session, workspace_id: int, base_name: str) -> str:
    try:
        root = normalize_stream_job_name(base_name or "job")
    except HTTPException:
        root = "job"
    root = root[:43].rstrip("-") or "job"
    candidate = f"{root}-copy"
    n = 1
    while (
        db.query(StreamingJob)
        .filter(StreamingJob.workspace_id == workspace_id, StreamingJob.name == candidate)
        .first()
    ):
        n += 1
        candidate = f"{root}-copy-{n}"
    return candidate


def _sql_script_for_submit(db: Session, job: StreamingJob, script: Optional[str] = None) -> str:
    from app.services.workspace_variables import substitute_script_variables

    raw = script if script is not None else (job.script_content or "")
    if (
        script is None
        and getattr(job, "definition_kind", None) == "pipeline"
        and getattr(job, "pipeline_spec", None)
    ):
        from app.services.stream_pipeline_runtime import resolve_pipeline_runtime

        runtime = resolve_pipeline_runtime(
            db,
            workspace_id=int(job.workspace_id or 0),
            spec=job.pipeline_spec,
        )
        raw = runtime.sql
        setattr(job, "_pipeline_secret_env", runtime.secret_env)
    return substitute_script_variables(db, int(job.workspace_id or 0), raw, "stream")


@router.post("/jobs/{job_id}/copy")
def copy_streaming_job(
    job_id: int,
    body: JobCopyBody = Body(default_factory=JobCopyBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """复制为新草稿作业（不含 Flink 运行时状态）。"""
    src = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    name = normalize_stream_job_name((body.name or "").strip() or _unique_streaming_job_copy_name(db, src.workspace_id, src.name))
    ensure_stream_job_name_available(db, int(src.workspace_id), name)
    job = StreamingJob(
        workspace_id=src.workspace_id,
        name=name,
        job_type=src.job_type,
        script_content=src.script_content,
        jar_path=None,
        main_class=src.main_class,
        program_args=src.program_args,
        parallelism=src.parallelism or 1,
        streaming_properties=getattr(src, "streaming_properties", None),
        flink_sql_submit_mode=_normalize_sql_submit_mode(getattr(src, "flink_sql_submit_mode", None)),
        flink_jar_submit_mode=_normalize_jar_submit_mode(getattr(src, "flink_jar_submit_mode", None)),
        flink_session_profile_id=getattr(src, "flink_session_profile_id", None),
        folder_id=getattr(src, "folder_id", None),
        jar_artifact_id=getattr(src, "jar_artifact_id", None),
        jar_version_id=getattr(src, "jar_version_id", None),
        connector_version_ids=getattr(src, "connector_version_ids", None),
        dependency_file_version_ids=getattr(src, "dependency_file_version_ids", None),
        definition_kind=getattr(src, "definition_kind", None) or ("jar" if src.job_type == "JAR" else "sql"),
        pipeline_spec=getattr(src, "pipeline_spec", None),
        compiler_version=getattr(src, "compiler_version", None),
        generated_artifact=getattr(src, "generated_artifact", None),
        spec_hash=getattr(src, "spec_hash", None),
        status="draft",
        is_locked=False,
        created_by=current_user.id,
        owner_id=current_user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _streaming_job_public_dict(db, job)


@router.put("/jobs/{job_id}")
def update_job(
    job_id: int,
    job_in: JobUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    create_history: bool = Query(
        True,
        description="是否写入作业版本历史。编辑器自动草稿保存应传 false（与 Studio create_history 对齐）。",
    ),
):
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    if getattr(job, "is_locked", False):
        raise HTTPException(status_code=403, detail="作业已锁定，请先解锁后再修改")
    patch = job_in.model_dump(exclude_unset=True)
    if "definition_kind" in patch and patch["definition_kind"] not in ("sql", "jar", "pipeline"):
        raise HTTPException(status_code=422, detail="definition_kind 须为 sql、jar 或 pipeline")
    effective_definition_kind = patch.get("definition_kind") or job.definition_kind
    if effective_definition_kind == "pipeline":
        effective_pipeline_spec = (
            patch["pipeline_spec"]
            if "pipeline_spec" in patch
            else getattr(job, "pipeline_spec", None)
        )
        if effective_pipeline_spec is None:
            raise HTTPException(status_code=422, detail="pipeline 作业必须提供 pipeline_spec")
        from app.api.stream_pipeline import (
            _job_previous_release_schema,
            _preflight_with_runtime,
        )
        from app.services.stream_pipeline_compiler import compile_pipeline
        from app.services.stream_pipeline_spec import PipelineSpec

        parsed_spec = PipelineSpec.model_validate(effective_pipeline_spec)
        check = _preflight_with_runtime(
            db,
            parsed_spec,
            workspace_id=int(job.workspace_id),
            previous_schema=_job_previous_release_schema(db, job),
        )
        if not check["ok"]:
            raise HTTPException(status_code=422, detail=check)
        patch["pipeline_spec"] = parsed_spec.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        artifact = compile_pipeline(parsed_spec)
        patch["definition_kind"] = "pipeline"
        patch["compiler_version"] = artifact["compiler_version"]
        patch["generated_artifact"] = artifact
        patch["spec_hash"] = artifact["spec_hash"]
        patch["script_content"] = artifact["sql"]
    elif "definition_kind" in patch:
        patch["pipeline_spec"] = None
        patch["compiler_version"] = None
        patch["generated_artifact"] = None
        patch["spec_hash"] = None
    if "name" in patch and patch["name"] is not None:
        new_name = normalize_stream_job_name(str(patch["name"]))
        if new_name != (job.name or ""):
            if (job.status or "").lower() == "running":
                raise HTTPException(status_code=409, detail="运行中的作业不支持重命名，请先停止或复制为新作业")
            ensure_stream_job_name_available(db, int(job.workspace_id), new_name, exclude_job_id=job.id)
        patch["name"] = new_name
    if "streaming_properties" in patch and patch["streaming_properties"] is not None:
        sp = str(patch["streaming_properties"]).strip()
        patch["streaming_properties"] = sp or None
    if "flink_session_profile_id" in patch:
        _require_flink_profile_in_workspace(db, job.workspace_id, patch.get("flink_session_profile_id"))
    watch = {
        "script_content",
        "main_class",
        "program_args",
        "parallelism",
        "streaming_properties",
        "flink_sql_submit_mode",
        "flink_jar_submit_mode",
        "flink_session_profile_id",
    }
    # 静默草稿不写历史；显式保存且字段确有变化时才快照
    if create_history and (watch & set(patch.keys())):
        changed = False
        for k in watch:
            if k not in patch:
                continue
            if getattr(job, k, None) != patch.get(k):
                changed = True
                break
        if changed:
            _append_streaming_job_history_snapshot(db, job, current_user.id)
    if not patch:
        return _streaming_job_public_dict(db, job)
    if set(patch.keys()) == {"script_content"} and (patch.get("script_content") or "") == (job.script_content or ""):
        return _streaming_job_public_dict(db, job)
    if "folder_id" in patch:
        _require_stream_folder(db, int(job.workspace_id), patch.get("folder_id"))
    if "jar_version_id" in patch and patch.get("jar_version_id") is not None:
        ver = db.query(StreamingJarVersion).filter(StreamingJarVersion.id == int(patch["jar_version_id"])).first()
        if not ver or ver.status != "active":
            raise HTTPException(status_code=400, detail="JAR 版本不存在或已废弃")
        art = db.query(StreamingJarArtifact).filter(StreamingJarArtifact.id == ver.artifact_id).first()
        if not art or art.workspace_id != job.workspace_id:
            raise HTTPException(status_code=400, detail="JAR 制品不属于当前工作空间")
        patch["jar_artifact_id"] = art.id
        if not (patch.get("main_class") or job.main_class) and ver.default_main_class:
            patch.setdefault("main_class", ver.default_main_class)
    if "connector_version_ids" in patch:
        ids = _parse_version_id_list(patch.get("connector_version_ids"))
        for vid in ids:
            ver = db.query(StreamingConnectorVersion).filter(StreamingConnectorVersion.id == vid).first()
            if not ver or ver.status != "active":
                raise HTTPException(status_code=400, detail=f"连接器版本 {vid} 不存在或已废弃")
            art = db.query(StreamingConnectorArtifact).filter(StreamingConnectorArtifact.id == ver.artifact_id).first()
            if not art or art.workspace_id != job.workspace_id:
                raise HTTPException(status_code=400, detail=f"连接器版本 {vid} 不属于当前工作空间")
        patch["connector_version_ids"] = _dump_version_id_list(ids)
    if "dependency_file_version_ids" in patch:
        ids = _parse_version_id_list(patch.get("dependency_file_version_ids"))
        for vid in ids:
            ver = db.query(StreamingFileVersion).filter(StreamingFileVersion.id == vid).first()
            if not ver or ver.status != "active":
                raise HTTPException(status_code=400, detail=f"依赖文件版本 {vid} 不存在或已废弃")
            art = db.query(StreamingFileArtifact).filter(StreamingFileArtifact.id == ver.artifact_id).first()
            if not art or art.workspace_id != job.workspace_id:
                raise HTTPException(status_code=400, detail=f"依赖文件版本 {vid} 不属于当前工作空间")
        patch["dependency_file_version_ids"] = _dump_version_id_list(ids)
    for k, v in patch.items():
        setattr(job, k, v)
    job.updated_at = datetime.utcnow()
    try:
        db.commit()
        db.refresh(job)
    except Exception as e:
        db.rollback()
        logging.getLogger(__name__).exception("update_job commit failed job_id=%s", job_id)
        msg = str(e)
        # 缺列时给可操作提示（重启 backend 会跑 ensure_columns 迁移）
        if "streaming_properties" in msg or "flink_sql_submit_mode" in msg or "flink_jar_submit_mode" in msg:
            raise HTTPException(
                status_code=500,
                detail=(
                    "保存版本历史失败：实时作业历史表缺少字段。"
                    "请重启 gido-backend 以自动补齐迁移后重试。"
                    f" ({type(e).__name__})"
                ),
            ) from e
        if "StringDataRightTruncation" in type(e).__name__ or "value too long for type character varying" in msg:
            raise HTTPException(
                status_code=500,
                detail=(
                    "保存失败：program_args 等字段超过数据库列长度。"
                    "请重启 gido-backend 以自动加宽 program_args 为 TEXT 后重试。"
                    f" ({type(e).__name__})"
                ),
            ) from e
        raise HTTPException(
            status_code=500,
            detail=f"保存作业失败：{type(e).__name__}: {msg[:400]}",
        ) from e
    return _streaming_job_public_dict(db, job)


@router.get("/jobs/{job_id}/history")
def list_streaming_job_history(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """最近 30 条逻辑快照（保存或提交前自动记录）。"""
    job = require_streaming_job(db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ)
    rows = (
        db.query(StreamingJobHistory)
        .filter(StreamingJobHistory.job_id == job_id)
        .order_by(StreamingJobHistory.id.desc())
        .limit(30)
        .all()
    )
    out = []
    for r in rows:
        u = db.query(User).filter(User.id == r.saved_by).first() if r.saved_by else None
        out.append(
            {
                "id": r.id,
                "job_type": r.job_type,
                "script_content": r.script_content,
                "main_class": r.main_class,
                "program_args": r.program_args,
                "parallelism": r.parallelism,
                "streaming_properties": getattr(r, "streaming_properties", None),
                "flink_sql_submit_mode": _normalize_sql_submit_mode(getattr(r, "flink_sql_submit_mode", None)),
                "saved_at": r.saved_at,
                "saved_by": r.saved_by,
                "saved_by_username": u.username if u else None,
            }
        )
    return out


_STREAMING_RELEASE_SNAPSHOT_FIELDS = tuple(
    field
    for field in _STREAMING_RELEASE_MODEL_SNAPSHOT_FIELDS
    if field not in ("content_hash", "release_note")
)


def _streaming_release_snapshot(
    job: StreamingJob, *, script_content: Optional[str] = None
) -> dict:
    script = job.script_content if script_content is None else script_content
    definition_kind = getattr(job, "definition_kind", None) or (
        "jar" if job.job_type == "JAR" else "sql"
    )
    pipeline_spec = getattr(job, "pipeline_spec", None)
    compiler_version = getattr(job, "compiler_version", None)
    generated_artifact = getattr(job, "generated_artifact", None)
    spec_hash = getattr(job, "spec_hash", None)
    if definition_kind == "pipeline":
        if script_content is not None:
            raise HTTPException(status_code=400, detail="pipeline 发布不接受 script_content 覆盖")
        if not pipeline_spec:
            raise HTTPException(status_code=400, detail="pipeline_spec 为空，无法创建发布")
        from app.services.stream_pipeline_compiler import compile_pipeline

        generated_artifact = compile_pipeline(pipeline_spec)
        compiler_version = generated_artifact["compiler_version"]
        spec_hash = generated_artifact["spec_hash"]
        script = generated_artifact["sql"]
    return {
        "job_name": job.name,
        "job_type": job.job_type,
        "script_content": script,
        "jar_path": job.jar_path,
        "main_class": job.main_class,
        "program_args": job.program_args,
        "parallelism": int(job.parallelism or 1),
        "streaming_properties": getattr(job, "streaming_properties", None),
        "flink_sql_submit_mode": _normalize_sql_submit_mode(
            getattr(job, "flink_sql_submit_mode", None)
        ),
        "flink_jar_submit_mode": _normalize_jar_submit_mode(
            getattr(job, "flink_jar_submit_mode", None)
        ),
        "flink_session_profile_id": getattr(job, "flink_session_profile_id", None),
        "jar_artifact_id": getattr(job, "jar_artifact_id", None),
        "jar_version_id": getattr(job, "jar_version_id", None),
        "connector_version_ids": _dump_version_id_list(
            getattr(job, "connector_version_ids", None)
        ),
        "dependency_file_version_ids": _dump_version_id_list(
            getattr(job, "dependency_file_version_ids", None)
        ),
        "definition_kind": definition_kind,
        "pipeline_spec": pipeline_spec,
        "compiler_version": compiler_version,
        "generated_artifact": generated_artifact,
        "spec_hash": spec_hash,
    }


def _streaming_release_hash(snapshot: dict) -> str:
    canonical = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_streaming_job_release(
    db: Session,
    job: StreamingJob,
    submitted_by: int,
    *,
    script_content: Optional[str] = None,
    release_note: Optional[str] = None,
) -> StreamingJobRelease:
    """创建不可变候选发布；仅 flush，由调用方控制事务与审批/部署。"""
    if getattr(job, "definition_kind", None) == "pipeline":
        from app.api.stream_pipeline import (
            StreamConnectionProfile,
            _job_previous_release_schema,
            _preflight_with_runtime,
        )
        from app.services.stream_pipeline_spec import PipelineSpec

        parsed_spec = PipelineSpec.model_validate(job.pipeline_spec)
        profile_ids = {
            parsed_spec.source.connection_profile_id,
            parsed_spec.sink.connection_profile_id,
        }
        (
            db.query(StreamConnectionProfile.id)
            .filter(StreamConnectionProfile.id.in_(profile_ids))
            .with_for_update()
            .all()
        )
        check = _preflight_with_runtime(
            db,
            parsed_spec,
            workspace_id=int(job.workspace_id),
            previous_schema=_job_previous_release_schema(db, job),
        )
        if not check["ok"]:
            raise HTTPException(status_code=422, detail=check)
    snapshot = _streaming_release_snapshot(job, script_content=script_content)
    if job.job_type == "SQL" and not (snapshot["script_content"] or "").strip():
        raise HTTPException(status_code=400, detail="SQL 内容为空，无法创建发布")
    if job.job_type not in ("SQL", "JAR"):
        raise HTTPException(status_code=400, detail=f"不支持的任务类型: {job.job_type}")
    # 串行化同一作业的版本号分配；唯一约束仍是最后一道保护。
    (
        db.query(StreamingJob.id)
        .filter(StreamingJob.id == job.id)
        .with_for_update()
        .first()
    )
    last_version = (
        db.query(func.max(StreamingJobRelease.version))
        .filter(StreamingJobRelease.job_id == job.id)
        .scalar()
    )
    release = StreamingJobRelease(
        job_id=job.id,
        version=int(last_version or 0) + 1,
        content_hash=_streaming_release_hash(snapshot),
        release_note=(release_note or "").strip() or None,
        approval_status="pending",
        submitted_by=submitted_by,
        submitted_at=datetime.utcnow(),
        **snapshot,
    )
    db.add(release)
    if not getattr(job, "current_running_release_id", None):
        job.lifecycle_state = "pending_approval"
    job.updated_at = datetime.utcnow()
    db.flush()
    return release


def approve_streaming_job_release(
    db: Session,
    job: StreamingJob,
    release: StreamingJobRelease,
    approved_by: int,
    *,
    comment: Optional[str] = None,
) -> StreamingJobRelease:
    """审批 helper；调用方必须先完成空间管理员/平台管理员鉴权。"""
    if int(release.job_id) != int(job.id):
        raise ValueError("release does not belong to job")
    if release.approval_status == "approved":
        return release
    if release.approval_status != "pending":
        raise HTTPException(status_code=409, detail="仅待审批发布可批准")
    release.approval_status = "approved"
    release.approved_by = approved_by
    release.approved_at = datetime.utcnow()
    release.approval_comment = (comment or "").strip() or None
    job.current_approved_release_id = release.id
    if not getattr(job, "current_running_release_id", None):
        job.lifecycle_state = "approved"
    job.updated_at = datetime.utcnow()
    db.flush()
    return release


def reject_streaming_job_release(
    db: Session,
    job: StreamingJob,
    release: StreamingJobRelease,
    reviewed_by: int,
    *,
    comment: Optional[str] = None,
) -> StreamingJobRelease:
    """拒绝 helper；快照内容保持不变。"""
    if int(release.job_id) != int(job.id):
        raise ValueError("release does not belong to job")
    if release.approval_status != "pending":
        raise HTTPException(status_code=409, detail="仅待审批发布可拒绝")
    release.approval_status = "rejected"
    release.approved_by = reviewed_by
    release.approved_at = datetime.utcnow()
    release.approval_comment = (comment or "").strip() or None
    if not getattr(job, "current_running_release_id", None):
        job.lifecycle_state = "draft"
    job.updated_at = datetime.utcnow()
    db.flush()
    return release


def _streaming_release_public_dict(
    db: Session, release: StreamingJobRelease, *, username_by_id: Optional[dict] = None
) -> dict:
    umap = username_by_id
    if umap is None:
        umap = _username_map(db, [release.submitted_by, release.approved_by])
    return {
        "id": release.id,
        "job_id": release.job_id,
        "version": release.version,
        **{field: getattr(release, field) for field in _STREAMING_RELEASE_SNAPSHOT_FIELDS},
        "connector_version_ids": _parse_version_id_list(release.connector_version_ids),
        "dependency_file_version_ids": _parse_version_id_list(
            release.dependency_file_version_ids
        ),
        "content_hash": release.content_hash,
        "release_note": release.release_note,
        "approval_status": release.approval_status,
        "submitted_by": release.submitted_by,
        "submitted_by_username": umap.get(release.submitted_by),
        "submitted_at": release.submitted_at,
        "approved_by": release.approved_by,
        "approved_by_username": umap.get(release.approved_by),
        "approved_at": release.approved_at,
        "approval_comment": release.approval_comment,
    }


def create_streaming_operation(
    db: Session,
    job: StreamingJob,
    operation_type: str,
    requested_by: int,
    *,
    release_id: Optional[int] = None,
    restore_point_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    request_payload: Optional[dict] = None,
) -> StreamingOperation:
    """创建生命周期审计；相同 idempotency_key 返回原记录。"""
    key = (idempotency_key or "").strip() or None
    if key:
        existing = (
            db.query(StreamingOperation)
            .filter(StreamingOperation.idempotency_key == key)
            .first()
        )
        if existing:
            if int(existing.job_id) != int(job.id):
                raise HTTPException(status_code=409, detail="幂等键已被其他作业使用")
            expected_request = (
                json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
                if request_payload is not None
                else None
            )
            if (
                existing.operation_type != operation_type
                or existing.release_id != release_id
                or existing.restore_point_id != restore_point_id
                or existing.request_json != expected_request
            ):
                raise HTTPException(
                    status_code=409,
                    detail="幂等键已用于不同的生命周期请求",
                )
            return existing
    operation = StreamingOperation(
        job_id=job.id,
        release_id=release_id,
        restore_point_id=restore_point_id,
        operation_type=operation_type,
        status="pending",
        idempotency_key=key,
        requested_by=requested_by,
        request_json=(
            json.dumps(request_payload, ensure_ascii=False, sort_keys=True)
            if request_payload is not None
            else None
        ),
    )
    db.add(operation)
    db.flush()
    return operation


def finish_streaming_operation(
    operation: StreamingOperation,
    *,
    status: str,
    result_payload: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> StreamingOperation:
    operation.status = status
    operation.completed_at = datetime.utcnow()
    operation.result_json = (
        json.dumps(result_payload, ensure_ascii=False, sort_keys=True)
        if result_payload is not None
        else None
    )
    operation.error_message = error_message
    return operation


def create_streaming_restore_point(
    db: Session,
    job: StreamingJob,
    created_by: int,
    *,
    release_id: Optional[int] = None,
    operation_id: Optional[int] = None,
    point_type: str = "savepoint",
    trigger_reason: Optional[str] = None,
) -> StreamingRestorePoint:
    restore = StreamingRestorePoint(
        job_id=job.id,
        release_id=release_id,
        operation_id=operation_id,
        point_type=point_type,
        status="pending",
        trigger_reason=trigger_reason,
        created_by=created_by,
    )
    db.add(restore)
    db.flush()
    return restore


def _streaming_restore_point_public_dict(row: StreamingRestorePoint) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "release_id": row.release_id,
        "operation_id": row.operation_id,
        "point_type": row.point_type,
        "status": row.status,
        "path": row.path,
        "flink_job_id": row.flink_job_id,
        "trigger_reason": row.trigger_reason,
        "metadata_json": row.metadata_json,
        "error_message": row.error_message,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


def _streaming_operation_public_dict(row: StreamingOperation) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "release_id": row.release_id,
        "restore_point_id": row.restore_point_id,
        "operation_type": row.operation_type,
        "status": row.status,
        "idempotency_key": row.idempotency_key,
        "requested_by": row.requested_by,
        "requested_at": row.requested_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "flink_job_id": row.flink_job_id,
        "flink_deployment_name": row.flink_deployment_name,
        "restore_path": row.restore_path,
        "request_json": row.request_json,
        "result_json": row.result_json,
        "error_message": row.error_message,
    }


def _approved_release_for_operation(
    db: Session,
    job: StreamingJob,
    release_id: Optional[int],
) -> StreamingJobRelease:
    query = db.query(StreamingJobRelease).filter(
        StreamingJobRelease.job_id == job.id,
        StreamingJobRelease.approval_status == "approved",
    )
    if release_id is not None:
        query = query.filter(StreamingJobRelease.id == release_id)
    elif getattr(job, "current_approved_release_id", None):
        query = query.filter(
            StreamingJobRelease.id == job.current_approved_release_id
        )
    else:
        query = query.order_by(StreamingJobRelease.version.desc())
    release = query.first()
    if not release:
        raise HTTPException(status_code=409, detail="请选择已批准的发布版本")
    return release


def _runtime_job_from_release(
    job: StreamingJob,
    release: StreamingJobRelease,
    *,
    parallelism: Optional[int] = None,
    streaming_properties: Optional[str] = None,
) -> StreamingJob:
    """构造不加入 Session 的运行快照，避免部署历史 release 覆盖 Studio 草稿。"""
    runtime_job = StreamingJob()
    for column in StreamingJob.__table__.columns:
        name = column.name
        if hasattr(job, name):
            setattr(runtime_job, name, getattr(job, name))
    release_to_job = {
        "job_name": "name",
        "job_type": "job_type",
        "script_content": "script_content",
        "jar_path": "jar_path",
        "main_class": "main_class",
        "program_args": "program_args",
        "parallelism": "parallelism",
        "streaming_properties": "streaming_properties",
        "flink_sql_submit_mode": "flink_sql_submit_mode",
        "flink_jar_submit_mode": "flink_jar_submit_mode",
        "flink_session_profile_id": "flink_session_profile_id",
        "jar_artifact_id": "jar_artifact_id",
        "jar_version_id": "jar_version_id",
        "connector_version_ids": "connector_version_ids",
        "dependency_file_version_ids": "dependency_file_version_ids",
        "definition_kind": "definition_kind",
        "pipeline_spec": "pipeline_spec",
        "compiler_version": "compiler_version",
        "generated_artifact": "generated_artifact",
        "spec_hash": "spec_hash",
    }
    for release_name, job_name in release_to_job.items():
        setattr(runtime_job, job_name, getattr(release, release_name))
    if parallelism is not None:
        runtime_job.parallelism = parallelism
    if streaming_properties is not None:
        runtime_job.streaming_properties = streaming_properties
    runtime_job.is_locked = False
    return runtime_job


def _copy_runtime_submit_result(
    runtime_job: StreamingJob,
    job: StreamingJob,
    *,
    release_id: int,
    lifecycle_state: str,
) -> None:
    for name in (
        "flink_job_id",
        "flink_operator_deployment_name",
        "flink_application_cluster_id",
        "flink_application_jm_rest",
        "last_submit_error",
        "last_submitted_at",
        "last_submitted_by",
        "status",
    ):
        setattr(job, name, getattr(runtime_job, name, None))
    job.current_running_release_id = release_id
    job.lifecycle_state = lifecycle_state
    job.updated_at = datetime.utcnow()


def _validate_streaming_runtime_capacity(
    parallelism: int,
    streaming_properties: Optional[str],
) -> None:
    from app.services.operator_resources import split_streaming_properties_for_operator

    props = _parse_job_streaming_properties(streaming_properties)
    _, resources = split_streaming_properties_for_operator(props or None)
    if resources.tm_replicas is not None:
        capacity = int(resources.task_slots) * int(resources.tm_replicas)
        if int(parallelism) > capacity:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"并行度 {parallelism} 超过显式资源容量 {capacity} "
                    f"（Task Slots {resources.task_slots} × TM 副本 {resources.tm_replicas}）"
                ),
            )


def _merge_release_runtime_properties(
    release_properties: Optional[str],
    override_properties: Optional[str],
) -> Optional[str]:
    if override_properties is None:
        return release_properties
    base = _parse_job_streaming_properties(release_properties)
    override = _parse_job_streaming_properties(override_properties)
    allowed_top_level = {"resource_tier", "operator_resources"}
    rejected = sorted(set(override) - allowed_top_level)
    if rejected:
        raise HTTPException(
            status_code=400,
            detail=f"运行时覆盖仅允许资源字段，不能修改已批准发布内容: {rejected}",
        )
    resources = override.get("operator_resources")
    if resources is not None:
        if not isinstance(resources, dict):
            raise HTTPException(status_code=400, detail="operator_resources 须为对象")
        allowed_resource_keys = {
            "jobManager",
            "taskManager",
            "taskSlots",
            "numberOfTaskSlots",
        }
        unknown_resources = sorted(set(resources) - allowed_resource_keys)
        if unknown_resources:
            raise HTTPException(
                status_code=400,
                detail=f"operator_resources 含不可覆盖字段: {unknown_resources}",
            )
        for section in ("jobManager", "taskManager"):
            value = resources.get(section)
            if value is None:
                continue
            if not isinstance(value, dict):
                raise HTTPException(
                    status_code=400, detail=f"operator_resources.{section} 须为对象"
                )
            allowed_nested = (
                {"memory", "cpu", "replicas"}
                if section == "taskManager"
                else {"memory", "cpu"}
            )
            unknown_nested = sorted(set(value) - allowed_nested)
            if unknown_nested:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"operator_resources.{section} 含不可覆盖字段: "
                        f"{unknown_nested}"
                    ),
                )
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return json.dumps(merged, ensure_ascii=False, sort_keys=True)


def _assert_durable_restore_path(path: str) -> str:
    value = (path or "").strip()
    if not value:
        raise HTTPException(status_code=409, detail="恢复点没有可用路径")
    durable_prefixes = (
        "s3://",
        "s3a://",
        "hdfs://",
        "oss://",
        "gs://",
        "abfs://",
        "abfss://",
        "file:///",
    )
    if not value.lower().startswith(durable_prefixes):
        raise HTTPException(
            status_code=409,
            detail="恢复点路径不是受支持的持久存储 URI",
        )
    return value


@router.post(
    "/jobs/{job_id}/releases",
    response_model=StreamingReleaseResponse,
    status_code=201,
)
def submit_streaming_job_release(
    job_id: int,
    body: ReleaseSubmitBody = Body(default_factory=ReleaseSubmitBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交不可变候选发布；不会部署、停止或重启 Flink 作业。"""
    job = require_streaming_job(
        db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE
    )
    release = create_streaming_job_release(
        db,
        job,
        current_user.id,
        script_content=body.script_content,
        release_note=body.release_note,
    )
    if workspace_data_full_control(db, current_user, job.workspace_id):
        approve_streaming_job_release(
            db,
            job,
            release,
            current_user.id,
            comment="管理员直接批准",
        )
    db.commit()
    db.refresh(release)
    return _streaming_release_public_dict(db, release)


@router.get(
    "/jobs/{job_id}/releases",
    response_model=List[StreamingReleaseResponse],
)
def list_streaming_job_releases(
    job_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_streaming_job(db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ)
    releases = (
        db.query(StreamingJobRelease)
        .filter(StreamingJobRelease.job_id == job_id)
        .order_by(StreamingJobRelease.version.desc())
        .limit(limit)
        .all()
    )
    umap = _username_map(
        db,
        [uid for r in releases for uid in (r.submitted_by, r.approved_by)],
    )
    return [
        _streaming_release_public_dict(db, release, username_by_id=umap)
        for release in releases
    ]


@router.get(
    "/jobs/{job_id}/releases/{release_id}",
    response_model=StreamingReleaseResponse,
)
def get_streaming_job_release(
    job_id: int,
    release_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_streaming_job(db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ)
    release = (
        db.query(StreamingJobRelease)
        .filter(
            StreamingJobRelease.id == release_id,
            StreamingJobRelease.job_id == job_id,
        )
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    return _streaming_release_public_dict(db, release)


@router.get(
    "/jobs/{job_id}/restore-points",
    response_model=List[StreamingRestorePointResponse],
)
def list_streaming_restore_points(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_streaming_job(db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ)
    rows = (
        db.query(StreamingRestorePoint)
        .filter(StreamingRestorePoint.job_id == job_id)
        .order_by(StreamingRestorePoint.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_streaming_restore_point_public_dict(row) for row in rows]


@router.get(
    "/jobs/{job_id}/operations",
    response_model=List[StreamingOperationResponse],
)
def list_streaming_operations(
    job_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_streaming_job(db, current_user, job_id, "viewer", PC.GIDO_STREAM_READ)
    rows = (
        db.query(StreamingOperation)
        .filter(StreamingOperation.job_id == job_id)
        .order_by(StreamingOperation.requested_at.desc())
        .limit(limit)
        .all()
    )
    return [_streaming_operation_public_dict(row) for row in rows]


def _execute_approved_release_deployment(
    db: Session,
    job: StreamingJob,
    release: StreamingJobRelease,
    current_user: User,
    *,
    parallelism: Optional[int],
    streaming_properties: Optional[str],
    restore_path: Optional[str] = None,
    allow_non_restored_state: bool = False,
) -> tuple[StreamingJob, dict]:
    sql_mode = _normalize_sql_submit_mode(release.flink_sql_submit_mode)
    jar_mode = _normalize_jar_submit_mode(release.flink_jar_submit_mode)
    if (release.job_type == "SQL" and sql_mode != "flink_operator") or (
        release.job_type == "JAR" and jar_mode != "flink_operator"
    ):
        raise HTTPException(
            status_code=409,
            detail="新运维生命周期仅支持 Flink Operator 发布版本",
        )
    effective_parallelism = int(parallelism or release.parallelism or 1)
    effective_properties = _merge_release_runtime_properties(
        release.streaming_properties,
        streaming_properties,
    )
    _validate_streaming_runtime_capacity(
        effective_parallelism,
        effective_properties,
    )
    runtime_job = _runtime_job_from_release(
        job,
        release,
        parallelism=effective_parallelism,
        streaming_properties=effective_properties,
    )
    result = execute_streaming_job_submit(
        db,
        runtime_job,
        current_user,
        restore_path=restore_path,
        savepoint_redeploy_nonce=(
            int(datetime.utcnow().timestamp() * 1000) if restore_path else None
        ),
        allow_non_restored_state=allow_non_restored_state,
        record_history=False,
        release_version=release.version,
        release_content_hash=release.content_hash,
    )
    return runtime_job, result


@router.post("/jobs/{job_id}/deploy")
def deploy_streaming_job_release(
    job_id: int,
    body: StreamingDeployBody,
    db: Session = Depends(get_db_flink),
    current_user: User = Depends(get_current_user),
):
    job = require_streaming_job(
        db, current_user, job_id, "developer", PC.GIDO_STREAM_RUN
    )
    if str(job.status or "").lower() == "running":
        raise HTTPException(status_code=409, detail="作业正在运行，请使用重启/恢复")
    release = _approved_release_for_operation(db, job, body.release_id)
    operation = create_streaming_operation(
        db,
        job,
        "deploy",
        current_user.id,
        release_id=release.id,
        idempotency_key=body.idempotency_key,
        request_payload=body.model_dump(),
    )
    if operation.status in ("succeeded", "failed"):
        return _streaming_operation_public_dict(operation)
    operation.status = "running"
    operation.started_at = datetime.utcnow()
    job.lifecycle_state = "DEPLOYING"
    db.commit()
    try:
        runtime_job, result = _execute_approved_release_deployment(
            db,
            job,
            release,
            current_user,
            parallelism=body.parallelism,
            streaming_properties=body.streaming_properties,
        )
        _copy_runtime_submit_result(
            runtime_job,
            job,
            release_id=release.id,
            lifecycle_state="RUNNING",
        )
        operation.flink_job_id = job.flink_job_id
        operation.flink_deployment_name = job.flink_operator_deployment_name
        finish_streaming_operation(
            operation,
            status="succeeded",
            result_payload=result,
        )
        db.commit()
        return {
            "message": f"发布版本 v{release.version} 已部署",
            "release_id": release.id,
            "operation_id": operation.id,
            **result,
        }
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        job.lifecycle_state = "DEPLOY_FAILED"
        job.last_submit_error = str(detail)
        finish_streaming_operation(
            operation,
            status="failed",
            error_message=str(detail),
        )
        db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"部署失败: {detail}") from exc


def _finalize_savepoint_stop(
    *,
    db: Session,
    job: StreamingJob,
    operation: StreamingOperation,
    restore: StreamingRestorePoint,
    deployment_name: str,
    namespace: str,
    timeout_seconds: float,
    previous_path: Optional[str],
    previous_trigger_id: Optional[str],
    previous_trigger_timestamp: Optional[str],
) -> dict:
    """等待 Savepoint 完成并回填；供同步 wait=true 与后台线程共用。"""
    from app.services.flink_operator_submit import (
        resume_flink_deployment,
        wait_for_completed_savepoint,
        wait_for_flink_deployment_running,
        wait_for_flink_deployment_suspended,
    )

    try:
        savepoint_path = wait_for_completed_savepoint(
            deployment_name,
            namespace,
            timeout_seconds=timeout_seconds,
            previous_path=previous_path,
            previous_trigger_id=previous_trigger_id,
            previous_trigger_timestamp=previous_trigger_timestamp,
        )
        final_cr = wait_for_flink_deployment_suspended(
            deployment_name,
            namespace,
            timeout_seconds=timeout_seconds,
        )
        restore.status = "completed"
        restore.path = _assert_durable_restore_path(savepoint_path)
        restore.flink_job_id = job.flink_job_id
        restore.completed_at = datetime.utcnow()
        restore.metadata_json = json.dumps(
            {
                "namespace": namespace,
                "deployment_name": deployment_name,
                "flink_version": (final_cr.get("spec") or {}).get("flinkVersion"),
                "parallelism": ((final_cr.get("spec") or {}).get("job") or {}).get(
                    "parallelism"
                ),
                "release_id": job.current_running_release_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        operation.restore_point_id = restore.id
        operation.restore_path = restore.path
        operation.flink_job_id = job.flink_job_id
        finish_streaming_operation(
            operation,
            status="succeeded",
            result_payload={"savepoint_path": restore.path},
        )
        job.status = "cancelled"
        job.lifecycle_state = "SUSPENDED"
        job.updated_at = datetime.utcnow()
        db.commit()
        return {
            "message": "Savepoint 已完成，作业已挂起",
            "operation_id": operation.id,
            "restore_point": _streaming_restore_point_public_dict(restore),
        }
    except Exception as exc:
        cluster_state = "unknown"
        try:
            resume_flink_deployment(
                deployment_name,
                namespace,
                upgrade_mode="savepoint",
            )
            after_resume = wait_for_flink_deployment_running(
                deployment_name,
                namespace,
                timeout_seconds=min(float(timeout_seconds), 60.0),
            )
            cluster_state = str(
                (((after_resume.get("spec") or {}).get("job") or {}).get("state"))
                or ""
            ).strip().lower() or "unknown"
        except Exception:
            cluster_state = "unknown"
        restore.status = "failed"
        restore.error_message = str(exc)
        restore.completed_at = datetime.utcnow()
        finish_streaming_operation(
            operation,
            status="failed",
            error_message=str(exc),
        )
        if cluster_state == "running":
            job.status = "running"
            job.lifecycle_state = "RUNNING"
        elif cluster_state == "suspended":
            job.status = "cancelled"
            job.lifecycle_state = "SUSPENDED"
        else:
            job.status = "failed"
            job.lifecycle_state = "STOP_FAILED"
        job.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(
            status_code=500,
            detail=(
                f"Savepoint 停止失败；集群状态={cluster_state}，"
                f"请在作业运维确认后重试: {exc}"
            ),
        ) from exc


def _finalize_savepoint_stop_background(
    *,
    job_id: int,
    operation_id: int,
    restore_id: int,
    deployment_name: str,
    namespace: str,
    timeout_seconds: float,
    previous_path: Optional[str],
    previous_trigger_id: Optional[str],
    previous_trigger_timestamp: Optional[str],
) -> None:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        job = db.query(StreamingJob).filter(StreamingJob.id == job_id).first()
        operation = db.query(StreamingOperation).filter(StreamingOperation.id == operation_id).first()
        restore = db.query(StreamingRestorePoint).filter(StreamingRestorePoint.id == restore_id).first()
        if not job or not operation or not restore:
            return
        if operation.status in ("succeeded", "failed"):
            return
        try:
            _finalize_savepoint_stop(
                db=db,
                job=job,
                operation=operation,
                restore=restore,
                deployment_name=deployment_name,
                namespace=namespace,
                timeout_seconds=timeout_seconds,
                previous_path=previous_path,
                previous_trigger_id=previous_trigger_id,
                previous_trigger_timestamp=previous_trigger_timestamp,
            )
        except HTTPException:
            # 失败态已写入库；后台线程不再向外抛
            logger.warning(
                "后台 Savepoint 停止失败 job_id=%s operation_id=%s",
                job_id,
                operation_id,
                exc_info=True,
            )
        except Exception:
            logger.exception(
                "后台 Savepoint 停止异常 job_id=%s operation_id=%s",
                job_id,
                operation_id,
            )
            try:
                job = db.query(StreamingJob).filter(StreamingJob.id == job_id).first()
                operation = db.query(StreamingOperation).filter(StreamingOperation.id == operation_id).first()
                restore = db.query(StreamingRestorePoint).filter(StreamingRestorePoint.id == restore_id).first()
                if restore and restore.status == "pending":
                    restore.status = "failed"
                    restore.error_message = "后台 Savepoint 停止异常"
                    restore.completed_at = datetime.utcnow()
                if operation and operation.status == "running":
                    finish_streaming_operation(
                        operation,
                        status="failed",
                        error_message="后台 Savepoint 停止异常",
                    )
                if job and str(job.lifecycle_state or "").upper() in ("SAVING_STATE", "SUSPENDING"):
                    job.lifecycle_state = "STOP_FAILED"
                    job.status = "failed"
                    job.updated_at = datetime.utcnow()
                db.commit()
            except Exception:
                logger.exception("回写后台停止失败态再次异常 job_id=%s", job_id)
    finally:
        db.close()


@router.post("/jobs/{job_id}/stop")
def stop_streaming_job_with_savepoint(
    job_id: int,
    body: StreamingStopBody = Body(default_factory=StreamingStopBody),
    db: Session = Depends(get_db_flink),
    current_user: User = Depends(get_current_user),
):
    """Savepoint 停止：默认异步提交（立刻返回），后台等待完成；wait=true 时同步等待。"""
    import threading

    from app.services.flink_operator_submit import (
        _operator_namespace,
        extract_savepoint_status_from_cr,
        extract_savepoint_trigger_from_cr,
        read_flink_deployment,
        suspend_flink_deployment,
    )

    job = require_streaming_job(
        db, current_user, job_id, "developer", PC.GIDO_STREAM_RUN
    )
    lifecycle = str(getattr(job, "lifecycle_state", None) or "").upper()
    if lifecycle in ("SAVING_STATE", "SUSPENDING"):
        raise HTTPException(
            status_code=409,
            detail="作业正在 Savepoint 停止中，请稍候查看状态或操作记录；勿重复点击",
        )
    deployment_name = _operator_deployment_name_for_job(job)
    if not deployment_name:
        raise HTTPException(status_code=409, detail="作业没有可挂起的 FlinkDeployment")
    namespace = _operator_namespace()
    try:
        before = read_flink_deployment(deployment_name, namespace)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"读取 FlinkDeployment 失败: {exc}") from exc
    flink_conf = (before.get("spec") or {}).get("flinkConfiguration") or {}
    savepoint_dir = (
        (settings.FLINK_OPERATOR_SAVEPOINT_DIR or "").strip()
        or str(flink_conf.get("state.savepoints.dir") or "").strip()
    )
    if not savepoint_dir:
        raise HTTPException(
            status_code=409,
            detail="未配置持久 Savepoint 目录，拒绝把默认停止退化为无状态停止",
        )
    _, previous_path, _ = extract_savepoint_status_from_cr(before)
    previous_trigger_id, previous_trigger_timestamp = (
        extract_savepoint_trigger_from_cr(before)
    )
    operation = create_streaming_operation(
        db,
        job,
        "stop",
        current_user.id,
        release_id=job.current_running_release_id,
        idempotency_key=body.idempotency_key,
        request_payload=body.model_dump(),
    )
    if operation.status in ("succeeded", "failed"):
        return _streaming_operation_public_dict(operation)
    restore = create_streaming_restore_point(
        db,
        job,
        current_user.id,
        release_id=job.current_running_release_id,
        operation_id=operation.id,
        trigger_reason="planned_stop",
    )
    operation.status = "running"
    operation.started_at = datetime.utcnow()
    operation.flink_deployment_name = deployment_name
    job.lifecycle_state = "SAVING_STATE"
    db.commit()
    try:
        suspend_flink_deployment(
            deployment_name,
            namespace,
            upgrade_mode="savepoint",
        )
        job.lifecycle_state = "SUSPENDING"
        db.commit()
    except Exception as exc:
        restore.status = "failed"
        restore.error_message = str(exc)
        restore.completed_at = datetime.utcnow()
        finish_streaming_operation(operation, status="failed", error_message=str(exc))
        job.lifecycle_state = "STOP_FAILED"
        job.status = "failed"
        job.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=f"触发 Savepoint 停止失败: {exc}") from exc

    finalize_kwargs = dict(
        deployment_name=deployment_name,
        namespace=namespace,
        timeout_seconds=float(body.timeout_seconds),
        previous_path=previous_path,
        previous_trigger_id=previous_trigger_id,
        previous_trigger_timestamp=previous_trigger_timestamp,
    )
    if body.wait:
        return _finalize_savepoint_stop(
            db=db,
            job=job,
            operation=operation,
            restore=restore,
            **finalize_kwargs,
        )

    threading.Thread(
        target=_finalize_savepoint_stop_background,
        kwargs={
            "job_id": int(job.id),
            "operation_id": int(operation.id),
            "restore_id": int(restore.id),
            **finalize_kwargs,
        },
        name=f"gido-savepoint-stop-{job.id}",
        daemon=True,
    ).start()
    return {
        "message": "已提交 Savepoint 停止，后台执行中；状态变为「正在保存/挂起」，完成后自动更新，可在操作记录查看进度",
        "operation_id": operation.id,
        "accepted": True,
        "lifecycle_state": job.lifecycle_state,
    }


@router.post("/jobs/{job_id}/restart")
def restart_streaming_job(
    job_id: int,
    body: StreamingRestartBody,
    db: Session = Depends(get_db_flink),
    current_user: User = Depends(get_current_user),
):
    from app.services.flink_operator_submit import (
        _operator_namespace,
        read_flink_deployment,
        resume_flink_deployment,
    )

    job = require_streaming_job(
        db, current_user, job_id, "developer", PC.GIDO_STREAM_RUN
    )
    release = _approved_release_for_operation(db, job, body.release_id)
    restore: Optional[StreamingRestorePoint] = None
    restore_path: Optional[str] = None
    if body.restore_mode == "stateless":
        if not body.confirm_stateless:
            raise HTTPException(
                status_code=400,
                detail="无状态启动会丢弃已有状态，必须显式确认",
            )
    else:
        restore_query = db.query(StreamingRestorePoint).filter(
            StreamingRestorePoint.job_id == job.id,
            StreamingRestorePoint.point_type == "savepoint",
            StreamingRestorePoint.status == "completed",
        )
        if body.restore_mode == "specific":
            if body.restore_point_id is None:
                raise HTTPException(status_code=400, detail="请选择恢复点")
            restore_query = restore_query.filter(
                StreamingRestorePoint.id == body.restore_point_id
            )
        else:
            restore_query = restore_query.order_by(
                StreamingRestorePoint.completed_at.desc()
            )
        restore = restore_query.first()
        if not restore:
            same_running_restart = (
                body.restore_mode == "latest"
                and str(job.status or "").lower() == "running"
                and int(job.current_running_release_id or 0) == int(release.id)
                and (
                    body.parallelism is None
                    or int(body.parallelism) == int(release.parallelism or 1)
                )
                and (
                    body.streaming_properties is None
                    or _parse_job_streaming_properties(body.streaming_properties)
                    == _parse_job_streaming_properties(release.streaming_properties)
                )
            )
            if not same_running_restart:
                raise HTTPException(status_code=409, detail="没有可用的成功 Savepoint")
        else:
            restore_path = _assert_durable_restore_path(restore.path or "")
            metadata = {}
            try:
                metadata = json.loads(restore.metadata_json or "{}")
            except Exception:
                metadata = {}
            saved_version = str(metadata.get("flink_version") or "").strip()
            runtime_version = str(settings.FLINK_OPERATOR_FLINK_VERSION or "").strip()
            if saved_version and runtime_version and saved_version != runtime_version:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"恢复点 Flink 版本 {saved_version} 与当前运行时 "
                        f"{runtime_version} 不兼容"
                    ),
                )
    prior_lifecycle = str(job.lifecycle_state or "").upper()
    prior_status = str(job.status or "").lower()
    operation_type = (
        "stateless-start" if body.restore_mode == "stateless" else "restart"
    )
    operation = create_streaming_operation(
        db,
        job,
        operation_type,
        current_user.id,
        release_id=release.id,
        restore_point_id=restore.id if restore else None,
        idempotency_key=body.idempotency_key,
        request_payload=body.model_dump(),
    )
    if operation.status in ("succeeded", "failed"):
        return _streaming_operation_public_dict(operation)
    operation.status = "running"
    operation.started_at = datetime.utcnow()
    operation.restore_path = restore_path
    job.lifecycle_state = "RESTORING"
    db.commit()
    try:
        same_release = int(job.current_running_release_id or 0) == int(release.id)
        parallelism_unchanged = (
            body.parallelism is None
            or int(body.parallelism) == int(release.parallelism or 1)
        )
        properties_unchanged = (
            body.streaming_properties is None
            or _parse_job_streaming_properties(body.streaming_properties)
            == _parse_job_streaming_properties(release.streaming_properties)
        )
        no_runtime_override = parallelism_unchanged and properties_unchanged
        deployment_name = _operator_deployment_name_for_job(job)
        can_patch_existing = False
        if deployment_name and (
            prior_status == "running" or prior_lifecycle == "SUSPENDED"
        ):
            try:
                current_cr = read_flink_deployment(
                    deployment_name,
                    _operator_namespace(),
                )
                current_state = str(
                    (((current_cr.get("spec") or {}).get("job") or {}).get("state"))
                    or ""
                ).strip().lower()
                can_patch_existing = (
                    (prior_status == "running" and current_state == "running")
                    or (
                        prior_lifecycle == "SUSPENDED"
                        and current_state == "suspended"
                    )
                )
            except Exception:
                can_patch_existing = False
        if (
            can_patch_existing
            and same_release
            and no_runtime_override
            and body.restore_mode == "latest"
        ):
            was_running = prior_status == "running"
            resume_flink_deployment(
                deployment_name,
                _operator_namespace(),
                restart_nonce=int(datetime.utcnow().timestamp() * 1000),
                upgrade_mode="savepoint",
            )
            result = {
                "message": (
                    "已通过 restartNonce 触发同配置有状态重启"
                    if was_running
                    else "已从最近 Savepoint 恢复 suspended FlinkDeployment"
                ),
                "flink_operator_deployment_name": deployment_name,
            }
            job.status = "running"
            job.lifecycle_state = "RUNNING"
        else:
            runtime_job, result = _execute_approved_release_deployment(
                db,
                job,
                release,
                current_user,
                parallelism=body.parallelism,
                streaming_properties=body.streaming_properties,
                restore_path=restore_path,
                allow_non_restored_state=body.allow_non_restored_state,
            )
            _copy_runtime_submit_result(
                runtime_job,
                job,
                release_id=release.id,
                lifecycle_state="RUNNING",
            )
        operation.flink_job_id = job.flink_job_id
        operation.flink_deployment_name = _operator_deployment_name_for_job(job)
        finish_streaming_operation(
            operation,
            status="succeeded",
            result_payload=result,
        )
        db.commit()
        return {
            "message": "作业已提交重启/恢复",
            "operation_id": operation.id,
            "release_id": release.id,
            "restore_point_id": restore.id if restore else None,
            **result,
        }
    except Exception as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        job.lifecycle_state = "RESTORE_FAILED"
        job.last_submit_error = str(detail)
        finish_streaming_operation(
            operation,
            status="failed",
            error_message=str(detail),
        )
        db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"恢复失败: {detail}") from exc


@router.post("/jobs/{job_id}/history/{history_id}/rollback")
def rollback_streaming_job_history(
    job_id: int, history_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """回滚到指定快照（需已解锁）。"""
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    if getattr(job, "is_locked", False):
        raise HTTPException(status_code=403, detail="作业已锁定，请先解锁后再回滚")
    rec = (
        db.query(StreamingJobHistory)
        .filter(StreamingJobHistory.id == history_id, StreamingJobHistory.job_id == job_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="版本不存在")
    job.script_content = rec.script_content
    job.main_class = rec.main_class
    job.program_args = rec.program_args
    if rec.parallelism is not None:
        job.parallelism = rec.parallelism
    if hasattr(rec, "streaming_properties"):
        job.streaming_properties = rec.streaming_properties
    sm = getattr(rec, "flink_sql_submit_mode", None)
    if sm:
        job.flink_sql_submit_mode = sm
    jm = getattr(rec, "flink_jar_submit_mode", None)
    if jm:
        job.flink_jar_submit_mode = jm
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return {"message": "已回滚到该版本", "job": _streaming_job_public_dict(db, job)}


@router.post("/jobs/{job_id}/unlock")
def unlock_streaming_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """负责人或空间管理员解锁后可继续编辑并提交。"""
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    oid = getattr(job, "owner_id", None) or job.created_by
    if current_user.id != oid and not workspace_data_full_control(db, current_user, job.workspace_id):
        raise HTTPException(status_code=403, detail="仅作业负责人或空间管理员可解锁")
    job.is_locked = False
    job.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return {"message": "已解锁", "job": _streaming_job_public_dict(db, job)}


@router.post("/jobs/{job_id}/submit", deprecated=True)
def submit_job(
    job_id: int,
    body: SubmitJobBody = Body(default_factory=SubmitJobBody),
    db: Session = Depends(get_db_flink),
    current_user: User = Depends(get_current_user),
):
    """兼容旧客户端的直接部署接口；新客户端应先创建 release，再从作业运维部署。"""
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_RUN)
    assert_can_publish_production(db, current_user, job.workspace_id)
    try:
        return execute_streaming_job_submit(db, job, current_user, script_content=body.script_content)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"提交失败: {e}")


def execute_streaming_job_submit(
    db: Session,
    job: StreamingJob,
    current_user: User,
    script_content: Optional[str] = None,
    *,
    restore_path: Optional[str] = None,
    savepoint_redeploy_nonce: Optional[int] = None,
    allow_non_restored_state: bool = False,
    record_history: bool = True,
    release_version: Optional[int] = None,
    release_content_hash: Optional[str] = None,
):
    """提交实时作业到 Flink（直接提交与审批通过后共用）。"""
    if getattr(job, "is_locked", False):
        raise HTTPException(status_code=403, detail="作业已锁定，请先解锁后再提交")
    if not getattr(job, "owner_id", None):
        job.owner_id = current_user.id
    incoming = script_content
    if incoming is not None:
        if record_history and (job.script_content or "") != (incoming or ""):
            _append_streaming_job_history_snapshot(db, job, current_user.id)
        job.script_content = incoming

    mode = "session"
    if job.job_type == "SQL":
        if not (job.script_content or "").strip():
            raise HTTPException(status_code=400, detail="SQL 内容为空")
        mode = _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None))
        if (
            getattr(job, "definition_kind", None) == "pipeline"
            and mode != "flink_operator"
        ):
            raise HTTPException(
                status_code=409,
                detail="数据管道仅允许通过 Flink Operator 提交，以使用运行时 Secret 注入",
            )
        fc = _flink_client_for_job(db, job)
        if mode == "flink_operator":
            from app.services.flink_operator_submit import sql_operator_submit_ready

            ok, reason = sql_operator_submit_ready()
            if not ok:
                raise HTTPException(status_code=400, detail=reason)
        elif mode == "kubernetes_application" and not (fc.k8s_application_image() or "").strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "K8s Application 提交需要 Flink 作业容器镜像。"
                    "请在当前作业绑定的 Flink Session 配置、或「系统管理 → 集成」、或环境变量 FLINK_K8S_APPLICATION_IMAGE 中配置后重试。"
                ),
            )
    elif job.job_type == "JAR":
        from app.services.jar_artifact import jar_artifact_exists, library_jar_exists

        jar_mode = _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None))
        lib_ver = None
        jvid = getattr(job, "jar_version_id", None)
        if jvid:
            lib_ver = db.query(StreamingJarVersion).filter(StreamingJarVersion.id == int(jvid)).first()
        has_lib = bool(
            lib_ver
            and lib_ver.status == "active"
            and library_jar_exists(int(lib_ver.artifact_id), int(lib_ver.version))
        )
        if jar_mode == "flink_operator":
            if not has_lib and not jar_artifact_exists(job.id):
                from app.services.artifact_s3 import artifact_s3_enabled

                hint = (
                    "请在「资源管理 → JAR 包」绑定版本，或重新上传 JAR。"
                    + (
                        " 已配置 S3 制品前缀时，Operator 从 s3:// 拉取；"
                        "否则经 HTTP 拉取，backend Pod 重启且未用 PVC 时会丢制品。"
                        if artifact_s3_enabled()
                        else " Operator 从 backend HTTP 拉取 artifact.jar；"
                        "若 backend Pod 曾重启且未用 PVC/S3，制品会丢失需重传。"
                    )
                )
                raise HTTPException(status_code=400, detail=f"JAR 制品不存在。{hint}")
        elif not has_lib and not job.jar_path and not jar_artifact_exists(job.id):
            raise HTTPException(status_code=400, detail="请先在「资源管理 → JAR 包」绑定版本或上传 JAR 文件")
        if jar_mode == "session":
            blocked = _jar_session_blocked_reason()
            if blocked:
                raise HTTPException(status_code=400, detail=blocked)
        if jar_mode == "flink_operator":
            if not (job.main_class or "").strip():
                raise HTTPException(status_code=400, detail="Flink Operator 生产提交须填写入口类（Main Class）")
            from app.services.flink_operator_submit import operator_submit_ready

            ok, reason = operator_submit_ready()
            if not ok:
                raise HTTPException(status_code=400, detail=reason)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的任务类型: {job.job_type}")

    sql_mode_exec = _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)) if job.job_type == "SQL" else ""
    if job.job_type == "SQL" and sql_mode_exec != "flink_operator":
        fc = _flink_client_for_job(db, job)
    elif job.job_type == "JAR":
        jar_mode = _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None))
        if jar_mode != "flink_operator":
            fc = _flink_client_for_job(db, job)
    try:
        submit_warning: Optional[str] = None
        job.flink_application_cluster_id = None
        job.flink_application_jm_rest = None
        job.flink_operator_deployment_name = None
        sql_to_run = _sql_script_for_submit(db, job) if job.job_type == "SQL" else None
        if job.job_type == "SQL":
            extra = _parse_job_streaming_properties(getattr(job, "streaming_properties", None))
            props_only, k8s_ov = _split_streaming_properties_for_sql(extra or None)
            from app.services.operator_resources import split_streaming_properties_for_operator

            flink_extra, op_resources = split_streaming_properties_for_operator(extra or None)
            flink_extra = _merge_connector_pipeline_jars(db, job, flink_extra)
            sql_source = str((extra or {}).get("sql_source") or "mount").strip().lower()
            if mode == "flink_operator":
                from app.services.flink_operator_submit import submit_sql_via_operator

                if record_history:
                    sql_version, sql_hash = _record_submit_history_version(db, job, current_user.id)
                else:
                    sql_version, sql_hash = release_version, release_content_hash
                dep_meta = _build_operator_deployment_meta(
                    job, user=current_user, sql_version=sql_version, sql_hash=sql_hash
                )
                out = submit_sql_via_operator(
                    job_id=job.id,
                    workspace_id=int(job.workspace_id or 0),
                    job_name=job.name,
                    sql_content=sql_to_run or "",
                    parallelism=job.parallelism or 1,
                    operator_resources=op_resources,
                    extra_flink_props=flink_extra or None,
                    deployment_meta=dep_meta,
                    sql_source=sql_source,
                    restore_path=restore_path,
                    savepoint_redeploy_nonce=savepoint_redeploy_nonce,
                    allow_non_restored_state=allow_non_restored_state,
                    runtime_secret_env=getattr(
                        job, "_pipeline_secret_env", None
                    ),
                )
                job.flink_operator_deployment_name = out.get("deployment_name")
                job.flink_application_jm_rest = out.get("application_jm_rest")
                fjid = (out.get("flink_job_id") or "").strip() or None
                job.flink_job_id = fjid
                submit_warning = out.get("warning")
                if submit_warning and not fjid:
                    job.last_submit_error = submit_warning
            elif mode == "kubernetes_application":
                out = fc.submit_sql_kubernetes_application(
                    sql_to_run or "", job.parallelism, props_only, k8s_ov, job.id
                )
                job.flink_application_cluster_id = out.get("cluster_id")
                job.flink_application_jm_rest = out.get("application_jm_rest")
                fjid = (out.get("flink_job_id") or "").strip() or None
                job.flink_job_id = fjid
                submit_warning = out.get("warning")
                if submit_warning and not fjid:
                    job.last_submit_error = submit_warning
            else:
                job.flink_job_id = fc.submit_sql(
                    sql_to_run or "", job.parallelism, extra_properties=props_only or None
                )
        elif job.job_type == "JAR":
            if record_history:
                _append_streaming_job_history_snapshot(db, job, current_user.id)
            jar_mode = _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None))
            if jar_mode == "flink_operator":
                from app.services.flink_operator_submit import submit_jar_via_operator
                from app.services.operator_resources import split_streaming_properties_for_operator

                jar_extra = _parse_job_streaming_properties(getattr(job, "streaming_properties", None))
                flink_extra, op_resources = split_streaming_properties_for_operator(jar_extra or None)
                flink_extra = _merge_connector_pipeline_jars(db, job, flink_extra)
                if record_history:
                    hist_version, _ = _record_submit_history_version(db, job, current_user.id)
                else:
                    hist_version = release_version
                dep_meta = _build_operator_deployment_meta(
                    job,
                    user=current_user,
                    sql_version=hist_version,
                    sql_hash=None,
                )
                out = submit_jar_via_operator(
                    job_id=job.id,
                    workspace_id=int(job.workspace_id or 0),
                    job_name=job.name,
                    entry_class=job.main_class or "",
                    parallelism=job.parallelism or 1,
                    program_args=job.program_args,
                    operator_resources=op_resources,
                    extra_flink_props=flink_extra or None,
                    deployment_meta=dep_meta,
                    jar_version_id=getattr(job, "jar_version_id", None),
                    jar_artifact_id=(int(lib_ver.artifact_id) if lib_ver else getattr(job, "jar_artifact_id", None)),
                    jar_version_num=(int(lib_ver.version) if lib_ver else None),
                    restore_path=restore_path,
                    savepoint_redeploy_nonce=savepoint_redeploy_nonce,
                    allow_non_restored_state=allow_non_restored_state,
                )
                job.flink_operator_deployment_name = out.get("deployment_name")
                job.flink_application_jm_rest = out.get("application_jm_rest")
                fjid = (out.get("flink_job_id") or "").strip() or None
                job.flink_job_id = fjid
                submit_warning = out.get("warning")
                if submit_warning and not fjid:
                    job.last_submit_error = submit_warning
            else:
                flink_job_id = fc.run_jar(job.jar_path, job.main_class, job.program_args, job.parallelism)
                job.flink_job_id = flink_job_id
        job.status = "running"
        sql_mode = _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None))
        jar_mode_done = _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None))
        pending_job_id = (
            (job.job_type == "SQL" and sql_mode in ("kubernetes_application", "flink_operator") and not job.flink_job_id)
            or (job.job_type == "JAR" and jar_mode_done == "flink_operator" and not job.flink_job_id)
        )
        if not pending_job_id:
            job.last_submit_error = None
        job.last_submitted_at = datetime.utcnow()
        job.last_submitted_by = current_user.id
        job.updated_at = datetime.utcnow()
        if settings.STUDIO_LOCK_ON_PUBLISH:
            job.is_locked = True
        db.commit()
        rt_cfg = _flink_runtime_cfg_for_job(db, job)
        op_dep_submit = _operator_deployment_name_for_job(job)
        submit_browser_jm = getattr(job, "flink_application_jm_rest", None)
        is_op_submit = (
            (jar_mode_done == "flink_operator" and job.job_type == "JAR")
            or (sql_mode == "flink_operator" and job.job_type == "SQL")
        ) and bool(op_dep_submit)
        if is_op_submit and op_dep_submit:
            from app.services.flink_operator_submit import browser_jm_base_for_deployment

            ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
            submit_browser_jm = browser_jm_base_for_deployment(
                op_dep_submit, ns, submit_browser_jm, job_id=int(job.id)
            ) or submit_browser_jm
        return {
            "message": "提交成功",
            "flink_job_id": job.flink_job_id,
            "flink_console_url": flink_job_console_url(
                job.flink_job_id,
                application_jm_rest=submit_browser_jm,
                runtime_cfg=rt_cfg,
                operator_mode=is_op_submit,
            ),
            "submit_warning": submit_warning,
            "flink_application_cluster_id": getattr(job, "flink_application_cluster_id", None),
            "flink_operator_deployment_name": getattr(job, "flink_operator_deployment_name", None),
            "is_locked": bool(getattr(job, "is_locked", False)),
        }
    except HTTPException:
        raise
    except Exception as e:
        err_full = str(e)
        try:
            from kubernetes.client import ApiException  # type: ignore

            if isinstance(e, ApiException):
                err_full = f"Kubernetes API HTTP {getattr(e, 'status', '?')}: {(e.body or e.reason or err_full)[:8000]}"
        except ImportError:
            pass
        if job.job_type == "JAR" and "ParameterTool" in err_full:
            err_full = (
                f"{err_full}\n\n提示：JAR 编译用的 Flink 版本与运行时（Session/Operator 镜像）不一致。"
                "请用 Flink 2.0.1 重新打包 JAR，或改用与 JAR 匹配的镜像与 flinkVersion。"
            )
        if len(err_full) > 32000:
            err_full = err_full[:32000] + "\n…(truncated)"
        job.status = "failed"
        job.last_submit_error = err_full
        job.updated_at = datetime.utcnow()
        db.commit()
        raise HTTPException(status_code=500, detail=f"提交失败: {e}")


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db_flink), current_user: User = Depends(get_current_user)):
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    if getattr(job, "is_locked", False) and not workspace_data_full_control(db, current_user, job.workspace_id):
        raise HTTPException(status_code=403, detail="作业已锁定，仅空间管理员或平台管理员可删除")
    op_dep_del = _operator_deployment_name_for_job(job)
    should_stop_flink = bool(job.flink_job_id or op_dep_del)
    if should_stop_flink:
        try:
            jm_ov = _jm_base_for_job(job)
            if op_dep_del:
                from app.services.flink_operator_submit import delete_flink_deployment

                delete_flink_deployment(op_dep_del)
                _release_operator_ui_tunnel(job)
                if job.job_type == "SQL" and _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)) == "flink_operator":
                    from app.services.flink_operator_submit import _operator_namespace
                    from app.services.sql_artifact import delete_sql_script_configmap

                    delete_sql_script_configmap(
                        job.id, int(job.workspace_id or 0), _operator_namespace()
                    )
            elif job.flink_job_id:
                _flink_client_for_job(db, job).cancel_job(job.flink_job_id, jm_base=jm_ov)
        except Exception:
            logger.warning(
                "删除前停止 Flink 任务失败 job_id=%s flink_job_id=%s deployment=%s",
                job.id,
                job.flink_job_id,
                getattr(job, "flink_operator_deployment_name", None),
                exc_info=True,
            )
    db.delete(job)
    db.commit()
    return {"message": "已删除"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db_flink), current_user: User = Depends(get_current_user)):
    """停止 Flink 任务。

    Operator 模式：删除与作业相关的全部 FlinkDeployment CR（库内名 + gido.io/job-id 标签），
    并校验删除后集群侧已无 CR；禁止「删错名字 → 404 却报成功」导致 Flink 仍在跑。
    """
    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_RUN)
    operation = create_streaming_operation(
        db,
        job,
        "force-stop",
        current_user.id,
        release_id=getattr(job, "current_running_release_id", None),
        request_payload={"confirmation": "ui-secondary-confirmation"},
    )
    operation.status = "running"
    operation.started_at = datetime.utcnow()
    db.flush()
    dep_name = _operator_deployment_name_for_job(job)
    is_operator = bool(dep_name) or (
        (job.job_type or "").upper() == "SQL"
        and _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)) == "flink_operator"
    ) or (
        (job.job_type or "").upper() == "JAR"
        and _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None)) == "flink_operator"
    )
    if is_operator:
        try:
            from app.services.flink_operator_submit import (
                _operator_namespace,
                delete_flink_deployment,
                find_flink_deployment_refs_for_job,
                wait_flink_deployment_reclaimed,
            )

            primary_ns = _operator_namespace()
            refs = find_flink_deployment_refs_for_job(
                int(job.id),
                workspace_id=int(job.workspace_id or 0) or None,
                preferred_name=dep_name,
                namespace=primary_ns,
            )
            # 强制停止只操作统一配置的 Operator namespace，不跨命名空间猜测。

            deleted_ok: List[str] = []
            deleted_ns: List[str] = []
            skipped_forbidden: List[str] = []
            for ns, name in refs:
                # 直接删 CR；403（无权 ns）跳过，勿中断整次停止
                try:
                    if delete_flink_deployment(name, namespace=ns):
                        deleted_ok.append(name)
                        if ns not in deleted_ns:
                            deleted_ns.append(ns)
                except PermissionError as e:
                    skipped_forbidden.append(f"{ns}/{name}")
                    logger.warning("cancel: skip forbidden delete %s/%s: %s", ns, name, e)

            # 再扫一遍候选空间，防止漏删
            more = find_flink_deployment_refs_for_job(
                int(job.id),
                workspace_id=int(job.workspace_id or 0) or None,
                preferred_name=None,
                namespace=primary_ns,
            )
            for ns, name in more:
                if (ns, name) in refs:
                    continue
                try:
                    if delete_flink_deployment(name, namespace=ns):
                        deleted_ok.append(name)
                        if ns not in deleted_ns:
                            deleted_ns.append(ns)
                        refs.append((ns, name))
                except PermissionError as e:
                    skipped_forbidden.append(f"{ns}/{name}")
                    logger.warning("cancel: skip forbidden delete %s/%s: %s", ns, name, e)

            # 只校验「实际尝试删除且非无权跳过」的目标
            check_refs = [(ns, name) for ns, name in refs if f"{ns}/{name}" not in skipped_forbidden]
            stuck: List[str] = []
            terminating: List[str] = []
            for ns, name in check_refs:
                state = wait_flink_deployment_reclaimed(name, namespace=ns, timeout_seconds=20.0)
                if state == "gone":
                    continue
                if state in ("terminating", "unknown"):
                    # Terminating=已受理；unknown=API 抖动，不阻断（平台已标停止，运维可对账）
                    if state == "terminating":
                        terminating.append(f"{ns}/{name}")
                    continue
                stuck.append(f"{ns}/{name}")

            if stuck:
                raise RuntimeError(
                    "已请求删除但 FlinkDeployment 仍未进入回收: "
                    + ", ".join(stuck)
                    + f"。当前停止命名空间为 {primary_ns}；"
                    "请核对 FLINK_OPERATOR_NAMESPACE 与 ServiceAccount RBAC。"
                )
            if not deleted_ok and not terminating and not check_refs and skipped_forbidden:
                raise RuntimeError(
                    f"停止失败：对 {primary_ns} 无删除权限 "
                    + f"（跳过: {', '.join(skipped_forbidden)}）。"
                    "请检查 Role/RoleBinding 是否允许 delete flinkdeployments。"
                )

            _release_operator_ui_tunnel(job)
            if job.job_type == "SQL" and _normalize_sql_submit_mode(getattr(job, "flink_sql_submit_mode", None)) == "flink_operator":
                from app.services.sql_artifact import delete_sql_script_configmap

                # SQL ConfigMap 与 FlinkDeployment 使用同一配置命名空间。
                for ns in sorted({primary_ns, *[n for n, _ in refs]}):
                    try:
                        delete_sql_script_configmap(job.id, int(job.workspace_id or 0), ns)
                    except Exception:
                        logger.debug("delete sql configmap skip ns=%s job=%s", ns, job.id, exc_info=True)

            primary = (deleted_ok[0] if deleted_ok else None) or (refs[0][1] if refs else dep_name)
            job.flink_operator_deployment_name = primary
            job.flink_job_id = None
            job.flink_application_jm_rest = None
            job.status = "cancelled"
            job.lifecycle_state = "FORCE_STOPPED"
            job.updated_at = datetime.utcnow()
            operation.flink_deployment_name = primary
            finish_streaming_operation(
                operation,
                status="succeeded",
                result_payload={
                    "deleted": deleted_ok,
                    "terminating": terminating,
                    "namespace": ",".join(deleted_ns or [primary_ns]),
                },
            )
            db.commit()
            ns_note = ",".join(deleted_ns or [primary_ns])
            if deleted_ok or terminating:
                note = ""
                if terminating:
                    note = f"；回收中: {', '.join(terminating)}"
                return {
                    "message": (
                        f"已停止：已删除 FlinkDeployment"
                        f"{(' (' + ', '.join(deleted_ok) + ')') if deleted_ok else ''}"
                        f"（namespace={ns_note}），Operator 将回收 JM/TM Pod{note}"
                    ),
                    "deleted": deleted_ok,
                    "terminating": terminating,
                    "namespace": ns_note,
                }
            return {
                "message": (
                    f"已停止：集群中已无对应 FlinkDeployment"
                    f"（已查 {primary_ns}），已将平台状态标为已停止"
                ),
                "deleted": [],
                "namespace": primary_ns,
            }
        except HTTPException:
            finish_streaming_operation(
                operation,
                status="failed",
                error_message="强制停止失败",
            )
            db.commit()
            raise
        except Exception as e:
            finish_streaming_operation(
                operation,
                status="failed",
                error_message=str(e),
            )
            db.commit()
            raise HTTPException(status_code=500, detail=f"停止失败: {e}")
    if not job.flink_job_id:
        if getattr(job, "flink_application_cluster_id", None):
            detail = (
                f"当前为 K8s Application 且尚未回填 jobId（clusterID={job.flink_application_cluster_id}）。"
                " 请在「系统管理 → 集成」配置 JM REST 模板（含 {cluster_id}）或环境变量 "
                "FLINK_K8S_APPLICATION_JM_REST_TEMPLATE 后重试，或在 Flink/K8s 控制台停止该集群。"
            )
            finish_streaming_operation(
                operation,
                status="failed",
                error_message=detail,
            )
            db.commit()
            raise HTTPException(
                status_code=400,
                detail=detail,
            )
        finish_streaming_operation(
            operation,
            status="failed",
            error_message="任务尚未提交",
        )
        db.commit()
        raise HTTPException(status_code=400, detail="任务尚未提交")
    try:
        jm_ov = _jm_base_for_job(job, prefer_stored=True, deadline_seconds=2.0)
        _flink_client_for_job(db, job).cancel_job(job.flink_job_id, jm_base=jm_ov)
        job.status = "cancelled"
        job.lifecycle_state = "FORCE_STOPPED"
        job.updated_at = datetime.utcnow()
        operation.flink_job_id = job.flink_job_id
        finish_streaming_operation(
            operation,
            status="succeeded",
            result_payload={"flink_job_id": job.flink_job_id},
        )
        db.commit()
        return {"message": "已停止"}
    except Exception as e:
        finish_streaming_operation(
            operation,
            status="failed",
            error_message=str(e),
        )
        db.commit()
        raise HTTPException(status_code=500, detail=f"停止失败: {e}")


def _summarize_checkpoints(raw: Optional[dict]) -> dict:
    """裁剪 Flink checkpoints REST 为运维摘要。"""
    raw = raw or {}
    counts = raw.get("counts") or {}
    latest = raw.get("latest") or {}
    completed = latest.get("completed") or {}
    failed = latest.get("failed") or {}
    in_progress = latest.get("in_progress") or latest.get("savepoint")  # 部分版本字段名不同

    def _one(block: dict) -> Optional[dict]:
        if not isinstance(block, dict) or not block:
            return None
        return {
            "id": block.get("id"),
            "status": block.get("status"),
            "path": block.get("external_path") or block.get("path"),
            "duration_ms": block.get("end_to_end_duration") or block.get("duration"),
            "size_bytes": block.get("state_size") or block.get("checkpointed_size"),
            "failure_message": block.get("failure_message") or block.get("failureMessage"),
            "trigger_timestamp": block.get("trigger_timestamp"),
        }

    # in_progress 可能是 dict 或嵌套
    ip = in_progress if isinstance(in_progress, dict) else None
    if ip is None and isinstance(latest.get("savepoint"), dict) and (latest.get("savepoint") or {}).get("status") == "IN_PROGRESS":
        ip = latest.get("savepoint")

    return {
        "counts": {
            "restored": counts.get("restored"),
            "total": counts.get("total"),
            "in_progress": counts.get("in_progress"),
            "completed": counts.get("completed"),
            "failed": counts.get("failed"),
        },
        "latest_completed": _one(completed if isinstance(completed, dict) else {}),
        "latest_failed": _one(failed if isinstance(failed, dict) else {}),
        "in_progress": _one(ip or {}),
    }


def _build_failure_payload(
    job: StreamingJob,
    *,
    flink_status: Optional[str] = None,
    note: Optional[str] = None,
    error: Optional[str] = None,
) -> Optional[dict]:
    """结构化失败原因：供运维诊断展示。"""
    st = (job.status or "").strip().lower()
    fl = (flink_status or "").strip().upper()
    submit_err = (getattr(job, "last_submit_error", None) or "").strip()
    note_s = (note or "").strip()
    err_s = (error or "").strip()
    interesting = st == "failed" or fl in ("FAILED", "FAILING") or bool(submit_err)
    if not interesting:
        return None
    source = "submit"
    message = submit_err or note_s or err_s or fl or st
    if note_s and (fl in ("FAILED", "FAILING") or "Operator" in note_s or note_s == submit_err):
        source = "cr"
        message = note_s or submit_err
    elif err_s and not submit_err:
        source = "jm"
        message = err_s
    elif fl in ("FAILED", "FAILING") and not submit_err:
        source = "jm"
        message = note_s or fl
    elif submit_err:
        source = "submit"
        message = submit_err
    return {
        "source": source,
        "message": (message or "")[:2000],
        "phase": fl or st or None,
    }


def _status_payload(job: StreamingJob, *, runtime_cfg: FlinkRuntimeConfig, **extra):
    out = {"status": job.status, "flink_operational": _compute_flink_operational(job, runtime_cfg=runtime_cfg)}
    out.update(extra)
    if "failure" not in out:
        failure = _build_failure_payload(
            job,
            flink_status=out.get("flink_status"),
            note=out.get("note"),
            error=out.get("error"),
        )
        if failure:
            out["failure"] = failure
    return out


def _apply_status_from_operator_cr(db: Session, job: StreamingJob, cr: Dict[str, Any], dep_name: str) -> Optional[dict]:
    """仅根据 K8s FlinkDeployment 回填 GIDO 状态；绝不因库内状态去改/删集群资源。"""
    from app.services.flink_operator_submit import extract_status_from_cr

    spec_state = (cr.get("spec", {}).get("job", {}).get("state") or "").strip().lower()
    jid, lifecycle, err = extract_status_from_cr(cr)
    lifecycle_up = (lifecycle or "").strip().upper()

    if spec_state == "suspended":
        if job.status != "cancelled" or getattr(job, "lifecycle_state", None) != "SUSPENDED":
            _mark_job_stopped(db, job, clear_runtime=True)
            job.flink_operator_deployment_name = dep_name
            job.lifecycle_state = "SUSPENDED"
            db.commit()
        return {
            "flink_status": "SUSPENDED",
            "note": "FlinkDeployment spec.job.state=suspended（集群侧已暂停）",
            "status": "cancelled",
        }

    # 集群 CR 仍在且非 suspended → GIDO 必须以集群为准（即使库内曾标 cancelled）
    if jid and job.flink_job_id != jid:
        job.flink_job_id = jid
        job.updated_at = datetime.utcnow()
        db.commit()

    if lifecycle_up in ("STABLE", "DEPLOYED", "CREATED", "RUNNING") or (
        jid and lifecycle_up not in ("FAILED", "FAILING", "")
    ):
        if job.status != "running" or getattr(job, "lifecycle_state", None) != "RUNNING":
            job.status = "running"
            job.lifecycle_state = "RUNNING"
            job.updated_at = datetime.utcnow()
            db.commit()
        if jid:
            return None  # 继续查 JM
        return {"flink_status": lifecycle_up or "RUNNING", "status": "running"}

    if err or lifecycle_up == "FAILED":
        flink_st = "FAILED"
        if job.status != "failed":
            job.status = "failed"
            if getattr(job, "lifecycle_state", None) != "RESTORE_FAILED":
                job.lifecycle_state = "FAILED"
            if err:
                job.last_submit_error = err
            job.updated_at = datetime.utcnow()
            db.commit()
        return {"flink_status": flink_st, "note": err or "Operator FAILED", "status": "failed"}

    if not job.flink_job_id:
        flink_st = lifecycle_up or "STARTING"
        if getattr(job, "lifecycle_state", None) in (None, "draft", "approved"):
            job.lifecycle_state = "DEPLOYING"
            job.updated_at = datetime.utcnow()
            db.commit()
        note = err or (f"Operator lifecycle: {lifecycle}" if lifecycle else "等待 Flink Job ID 回填")
        return {"flink_status": flink_st, "note": note, "status": job.status}
    return None


def _sync_one_job_live_status(
    db: Session,
    job: StreamingJob,
    *,
    cr_cache: Optional[Dict[str, Any]] = None,
    jm_timeout: float = 3.0,
) -> dict:
    """以 K8s/JM 为准回填 GIDO 库内状态；同步路径禁止删除/修改集群 CR。"""
    rt_cfg = _flink_runtime_cfg_for_job(db, job)
    fc = _flink_client_for_job(db, job)

    jm_ov = _jm_base_for_job(job, prefer_stored=True, deadline_seconds=2.0)
    if jm_ov and jm_ov != (getattr(job, "flink_application_jm_rest", None) or "").strip().rstrip("/"):
        job.flink_application_jm_rest = jm_ov
    dep_name = _operator_deployment_name_for_job(job)
    if dep_name:
        cr = (cr_cache or {}).get(dep_name) if cr_cache is not None else None
        cr_listed_missing = False
        if cr is None and cr_cache is not None:
            cr_listed_missing = True
        if cr is None and cr_cache is None:
            try:
                from app.services.flink_operator_submit import read_flink_deployment

                cr = read_flink_deployment(dep_name)
            except Exception as ex:
                from kubernetes.client import ApiException  # type: ignore

                if isinstance(ex, ApiException) and getattr(ex, "status", None) == 404:
                    cr_listed_missing = True
                else:
                    logger.debug("读取 FlinkDeployment 状态失败 job_id=%s dep=%s", job.id, dep_name, exc_info=True)
                    cr = None
        if cr is not None:
            try:
                early = _apply_status_from_operator_cr(db, job, cr, dep_name)
                if early is not None:
                    return _status_payload(job, runtime_cfg=rt_cfg, **early)
            except Exception:
                logger.debug("Operator CR 状态解析失败 job_id=%s dep=%s", job.id, dep_name, exc_info=True)
        elif cr_listed_missing:
            # 仅回填 GIDO：集群已无 CR → 平台显示已停止（不删任何东西）
            if (job.status or "").lower() not in ("cancelled", "finished", "failed"):
                _mark_job_stopped(db, job, clear_runtime=True)
                job.flink_operator_deployment_name = dep_name
                db.commit()
            return _status_payload(
                job,
                runtime_cfg=rt_cfg,
                flink_status="NOT_FOUND_ON_OPERATOR",
                note="集群中已无对应 FlinkDeployment，已将平台状态回填为已停止",
                status="cancelled",
            )
        elif not job.flink_job_id:
            if job.status == "cancelled":
                return _status_payload(job, runtime_cfg=rt_cfg, flink_status="UNKNOWN", status="cancelled")
            return _status_payload(job, runtime_cfg=rt_cfg, flink_status="UNKNOWN", note="无法读取 FlinkDeployment")

    if not job.flink_job_id:
        cid = getattr(job, "flink_application_cluster_id", None)
        if cid and (getattr(job, "flink_sql_submit_mode", None) or "session").strip().lower() == "kubernetes_application":
            return _status_payload(
                job,
                runtime_cfg=rt_cfg,
                flink_status="APPLICATION_PENDING_JOB_ID",
                cluster_id=cid,
                note="Application 已创建，尚未回填 jobId；在集成页或 FLINK_K8S_APPLICATION_JM_REST_TEMPLATE 配置 JM REST 模板后可自动同步状态。",
            )
        return _status_payload(job, runtime_cfg=rt_cfg, flink_status=None, status=job.status)
    try:
        detail = fc.fetch_job_document(job.flink_job_id, jm_base=jm_ov, timeout=jm_timeout)
        if detail is None:
            if job.status == "running":
                _mark_job_stopped(db, job, clear_runtime=False)
            return _status_payload(
                job,
                runtime_cfg=rt_cfg,
                flink_status="NOT_FOUND_ON_JM",
                detail=None,
                note="Flink JobManager 上已无该 Job ID，已将平台状态回填为已停止",
                status=job.status,
            )
        flink_state = (detail.get("state") or "UNKNOWN").upper()
        state_map = {
            "RUNNING": "running",
            "INITIALIZING": "running",
            "CREATED": "running",
            "RESTARTING": "running",
            "RECONCILING": "running",
            "SCHEDULED": "running",
            "RECONNECTING": "running",
            "FAILING": "running",
            "FINISHED": "finished",
            "FAILED": "failed",
            "CANCELED": "cancelled",
            "CANCELLED": "cancelled",
            "CANCELLING": "cancelled",
        }
        new_status = state_map.get(flink_state)
        # 集群 JM 仍 RUNNING → 纠正库内错误的 cancelled（GIDO 依赖 K8s，不做反向关闭）
        if new_status:
            job.status = new_status
        job.updated_at = datetime.utcnow()
        db.commit()
        return _status_payload(job, runtime_cfg=rt_cfg, flink_status=flink_state, detail=detail, status=job.status)
    except Exception as e:
        return _status_payload(job, runtime_cfg=rt_cfg, flink_status="UNKNOWN", error=str(e), status=job.status)


@router.get("/jobs-status-sync")
def sync_workspace_jobs_status(
    workspace_id: int,
    db: Session = Depends(get_db_flink),
    current_user: User = Depends(get_current_user),
):
    """
    工作空间批量状态同步：一次 list FlinkDeployment，**仅用集群状态回填 GIDO**。
    同步路径不会删除/修改 FlinkDeployment；停止集群资源只发生在用户点击「停止」。
    """
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    jobs = (
        db.query(StreamingJob)
        .filter(StreamingJob.workspace_id == workspace_id)
        .order_by(StreamingJob.sort_order.asc(), StreamingJob.id.asc())
        .all()
    )
    need = [j for j in jobs if _job_needs_live_status_sync(j)]
    cr_cache: Optional[Dict[str, Any]] = None
    if any(_operator_deployment_name_for_job(j) for j in need):
        cr_cache = {}
        try:
            from app.services.flink_operator_submit import list_flink_deployments

            for cr in list_flink_deployments(workspace_id=workspace_id):
                name = ((cr.get("metadata") or {}).get("name") or "").strip()
                if name:
                    cr_cache[name] = cr
        except Exception:
            logger.debug("jobs-status-sync list CR failed workspace=%s", workspace_id, exc_info=True)
            cr_cache = None  # 回退到单作业 read CR

    items = []
    for job in need:
        try:
            payload = _sync_one_job_live_status(db, job, cr_cache=cr_cache, jm_timeout=3.0)
        except Exception as ex:
            payload = {"status": job.status, "flink_status": "UNKNOWN", "error": str(ex)}
        items.append(
            {
                "id": job.id,
                "status": payload.get("status", job.status),
                "flink_status": payload.get("flink_status"),
                "note": payload.get("note"),
                "error": payload.get("error"),
                "failure": payload.get("failure"),
            }
        )
    return {"items": items, "synced": len(items), "total": len(jobs)}


@router.get("/jobs/{job_id}/status")
def get_job_status(job_id: int, db: Session = Depends(get_db_flink), current_user: User = Depends(get_current_user)):
    """同步 Flink 任务状态；与 JobManager REST 对齐，JM 已无记录时回填平台为非运行态"""
    job = require_streaming_job(db, current_user, job_id)
    return _sync_one_job_live_status(db, job, jm_timeout=4.0)


@router.get("/jobs/{job_id}/exceptions")
def get_job_exceptions(job_id: int, db: Session = Depends(get_db_flink), current_user: User = Depends(get_current_user)):
    """获取 Flink 任务异常信息"""
    job = require_streaming_job(db, current_user, job_id)
    if not job.flink_job_id:
        return {"exceptions": []}
    jm_ov = _jm_base_for_job(job, prefer_stored=True, deadline_seconds=2.0)
    fc = _flink_client_for_job(db, job)
    try:
        return fc.job_exceptions(job.flink_job_id, jm_base=jm_ov)
    except Exception as e:
        return {"exceptions": [], "error": str(e)}


@router.get("/jobs/{job_id}/checkpoints")
def get_job_checkpoints(job_id: int, db: Session = Depends(get_db_flink), current_user: User = Depends(get_current_user)):
    """只读 Checkpoint 摘要（不触发 savepoint）。"""
    job = require_streaming_job(db, current_user, job_id)
    if not job.flink_job_id:
        return {
            "available": False,
            "reason": "尚无 Flink Job ID，无法拉取 checkpoints",
            **_summarize_checkpoints(None),
        }
    jm_ov = _jm_base_for_job(job, prefer_stored=True, deadline_seconds=2.0)
    fc = _flink_client_for_job(db, job)
    try:
        raw = fc.job_checkpoints(job.flink_job_id, jm_base=jm_ov, timeout=8.0)
        return {"available": True, "flink_job_id": job.flink_job_id, **_summarize_checkpoints(raw)}
    except Exception as e:
        return {
            "available": False,
            "reason": str(e),
            "flink_job_id": job.flink_job_id,
            **_summarize_checkpoints(None),
        }


@router.get("/jobs/{job_id}/flink-ui/bootstrap")
def bootstrap_operator_flink_ui_proxy(
    job_id: int,
    access_token: str = Query(..., description="GIDO 登录 JWT"),
    then: Optional[str] = Query(None, description="代理就绪后跳转的 Flink UI 路径（含 # 路由）"),
    db: Session = Depends(get_db_flink),
):
    """签发 Flink UI 代理 Cookie 并跳转；供浏览器新标签打开（无需 kubectl port-forward JM）。"""
    from app.core.security import get_user_from_access_token
    from app.services.flink_operator_ui_proxy import (
        issue_ui_proxy_cookie,
        operator_ui_proxy_enabled,
        operator_ui_proxy_prefix,
    )

    if not operator_ui_proxy_enabled():
        raise HTTPException(status_code=404, detail="Flink UI 代理未启用")
    user = get_user_from_access_token(access_token, db)
    job = require_streaming_job(db, user, job_id)
    dep = _operator_deployment_name_for_job(job)
    if not dep:
        raise HTTPException(status_code=400, detail="非 Flink Operator 作业")

    prefix = operator_ui_proxy_prefix(job_id)
    target = (then or prefix).strip()
    if not target.startswith(prefix):
        raise HTTPException(status_code=400, detail="非法跳转路径")

    cookie_name, cookie_value = issue_ui_proxy_cookie(job_id, int(user.id))
    resp = RedirectResponse(url=target, status_code=302)
    from app.services.flink_operator_ui_proxy import _proxy_cookie_path

    resp.set_cookie(
        key=cookie_name,
        value=cookie_value,
        path=_proxy_cookie_path(job_id),
        httponly=True,
        samesite="lax",
        max_age=4 * 3600,
    )
    return resp


@router.api_route(
    "/jobs/{job_id}/flink-ui",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@router.api_route(
    "/jobs/{job_id}/flink-ui/{subpath:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
def proxy_operator_flink_ui(
    job_id: int,
    request: Request,
    subpath: str = "",
    db: Session = Depends(get_db_flink),
):
    """反向代理 Operator JM Web UI（集群内 DNS），浏览器仅访问 GIDO。"""
    from app.services.flink_operator_ui_proxy import (
        _PROXY_COOKIE,
        operator_ui_proxy_enabled,
        proxy_flink_ui_request,
        validate_ui_proxy_cookie,
    )

    if not operator_ui_proxy_enabled():
        raise HTTPException(status_code=404, detail="Flink UI 代理未启用")
    if not validate_ui_proxy_cookie(job_id, request.cookies.get(_PROXY_COOKIE)):
        raise HTTPException(status_code=401, detail="请从 GIDO 作业页点击打开 Flink UI")

    job = db.query(StreamingJob).filter(StreamingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="作业不存在")
    dep = _operator_deployment_name_for_job(job)
    if not dep:
        raise HTTPException(status_code=400, detail="非 Flink Operator 作业")

    ns = (settings.FLINK_OPERATOR_NAMESPACE or settings.FLINK_K8S_NAMESPACE or "flink").strip()
    qs = str(request.url.query) if request.url.query else ""
    status_code, headers, body = proxy_flink_ui_request(
        job_id=job_id,
        deployment_name=dep,
        namespace=ns,
        stored_jm_rest=getattr(job, "flink_application_jm_rest", None),
        subpath=subpath,
        method=request.method,
        query_string=qs,
        incoming_headers=dict(request.headers),
    )
    if request.method.upper() == "HEAD":
        return Response(status_code=status_code, headers=headers)
    return Response(content=body, status_code=status_code, headers=headers)


@router.get("/jar-versions/{version_id}/artifact.jar")
def download_library_jar_version(
    version_id: int,
    token: str = Query(..., description="与 FLINK_OPERATOR_ARTIFACT_TOKEN 一致"),
    db: Session = Depends(get_db),
):
    """供 Flink Operator 拉取制品库版本 JAR。"""
    from app.services.jar_artifact import (
        artifact_download_token_is_valid,
        ensure_library_version_file,
    )

    if not artifact_download_token_is_valid(token):
        raise HTTPException(status_code=403, detail="无效 artifact token")
    ver = db.query(StreamingJarVersion).filter(StreamingJarVersion.id == version_id).first()
    if not ver:
        raise HTTPException(status_code=404, detail="JAR 版本不存在")
    path = ensure_library_version_file(int(ver.artifact_id), int(ver.version))
    if not path:
        raise HTTPException(status_code=404, detail="JAR 文件不存在")
    return FileResponse(path, media_type="application/java-archive", filename=ver.file_name or "artifact.jar")


def _serialize_jar_version(db: Session, ver: StreamingJarVersion, username_by_id: Optional[dict] = None) -> dict:
    umap = username_by_id or {}
    uid = getattr(ver, "uploaded_by", None)
    return {
        "id": ver.id,
        "artifact_id": ver.artifact_id,
        "version": ver.version,
        "file_name": ver.file_name,
        "size_bytes": ver.size_bytes,
        "sha256": ver.sha256,
        "default_main_class": ver.default_main_class,
        "change_note": ver.change_note,
        "status": ver.status,
        "uploaded_by": uid,
        "uploaded_by_username": umap.get(uid) if uid else None,
        "uploaded_at": ver.uploaded_at,
    }


def _serialize_jar_artifact(db: Session, art: StreamingJarArtifact, *, include_versions: bool = False) -> dict:
    refs = db.query(StreamingJob).filter(StreamingJob.jar_artifact_id == art.id).count()
    latest = (
        db.query(StreamingJarVersion)
        .filter(StreamingJarVersion.artifact_id == art.id)
        .order_by(StreamingJarVersion.version.desc())
        .first()
    )
    uids = [art.owner_id, art.created_by, latest.uploaded_by if latest else None]
    umap = _username_map(db, uids)
    out = {
        "id": art.id,
        "workspace_id": art.workspace_id,
        "name": art.name,
        "description": art.description,
        "owner_id": art.owner_id,
        "owner_username": umap.get(art.owner_id) if art.owner_id else None,
        "created_by": art.created_by,
        "created_by_username": umap.get(art.created_by) if art.created_by else None,
        "created_at": art.created_at,
        "updated_at": art.updated_at,
        "ref_job_count": refs,
        "latest_version": _serialize_jar_version(db, latest, umap) if latest else None,
    }
    if include_versions:
        vers = (
            db.query(StreamingJarVersion)
            .filter(StreamingJarVersion.artifact_id == art.id)
            .order_by(StreamingJarVersion.version.desc())
            .all()
        )
        vuids = [v.uploaded_by for v in vers]
        vumap = _username_map(db, vuids)
        out["versions"] = [_serialize_jar_version(db, v, vumap) for v in vers]
    return out


def _serialize_jar_artifacts_list(db: Session, arts: List[StreamingJarArtifact]) -> List[dict]:
    """列表页批量序列化，避免每个制品单独 count/latest 查询。"""
    if not arts:
        return []
    ids = [a.id for a in arts]
    ref_rows = (
        db.query(StreamingJob.jar_artifact_id, func.count(StreamingJob.id))
        .filter(StreamingJob.jar_artifact_id.in_(ids))
        .group_by(StreamingJob.jar_artifact_id)
        .all()
    )
    ref_map = {int(aid): int(cnt) for aid, cnt in ref_rows if aid is not None}
    vers = (
        db.query(StreamingJarVersion)
        .filter(StreamingJarVersion.artifact_id.in_(ids))
        .order_by(StreamingJarVersion.artifact_id.asc(), StreamingJarVersion.version.desc())
        .all()
    )
    latest_map: Dict[int, StreamingJarVersion] = {}
    for v in vers:
        if v.artifact_id not in latest_map:
            latest_map[int(v.artifact_id)] = v
    uids: List[Optional[int]] = []
    for a in arts:
        uids.extend([a.owner_id, a.created_by])
        lv = latest_map.get(a.id)
        if lv:
            uids.append(lv.uploaded_by)
    umap = _username_map(db, uids)
    out = []
    for a in arts:
        latest = latest_map.get(a.id)
        out.append(
            {
                "id": a.id,
                "workspace_id": a.workspace_id,
                "name": a.name,
                "description": a.description,
                "owner_id": a.owner_id,
                "owner_username": umap.get(a.owner_id) if a.owner_id else None,
                "created_by": a.created_by,
                "created_by_username": umap.get(a.created_by) if a.created_by else None,
                "created_at": a.created_at,
                "updated_at": a.updated_at,
                "ref_job_count": ref_map.get(a.id, 0),
                "latest_version": _serialize_jar_version(db, latest, umap) if latest else None,
            }
        )
    return out


class JarArtifactCreate(BaseModel):
    workspace_id: int
    name: str
    description: Optional[str] = None


class JarArtifactUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@router.get("/jar-artifacts")
def list_jar_artifacts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_READ)
    rows = (
        db.query(StreamingJarArtifact)
        .filter(StreamingJarArtifact.workspace_id == workspace_id)
        .order_by(StreamingJarArtifact.updated_at.desc())
        .all()
    )
    return _serialize_jar_artifacts_list(db, rows)


@router.post("/jar-artifacts")
def create_jar_artifact(
    body: JarArtifactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="制品名称不能为空")
    art = StreamingJarArtifact(
        workspace_id=body.workspace_id,
        name=name,
        description=(body.description or "").strip() or None,
        owner_id=current_user.id,
        created_by=current_user.id,
    )
    db.add(art)
    db.commit()
    db.refresh(art)
    return _serialize_jar_artifact(db, art)


@router.get("/jar-artifacts/{artifact_id}")
def get_jar_artifact(artifact_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    art = db.query(StreamingJarArtifact).filter(StreamingJarArtifact.id == artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="制品不存在")
    assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_READ)
    return _serialize_jar_artifact(db, art, include_versions=True)


@router.put("/jar-artifacts/{artifact_id}")
def update_jar_artifact(
    artifact_id: int,
    body: JarArtifactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    art = db.query(StreamingJarArtifact).filter(StreamingJarArtifact.id == artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="制品不存在")
    assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="制品名称不能为空")
        art.name = name
    if body.description is not None:
        art.description = body.description.strip() or None
    art.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(art)
    return _serialize_jar_artifact(db, art, include_versions=True)


@router.delete("/jar-artifacts/{artifact_id}")
def delete_jar_artifact(artifact_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    art = db.query(StreamingJarArtifact).filter(StreamingJarArtifact.id == artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="制品不存在")
    assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    refs = db.query(StreamingJob).filter(StreamingJob.jar_artifact_id == artifact_id).count()
    if refs:
        raise HTTPException(status_code=409, detail=f"仍有 {refs} 个作业引用该制品，请先解绑后再删除")
    db.delete(art)
    db.commit()
    return {"message": "删除成功"}


@router.post("/jar-artifacts/{artifact_id}/versions")
async def upload_jar_artifact_version(
    artifact_id: int,
    file: UploadFile = File(...),
    change_note: Optional[str] = Query(None),
    default_main_class: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import hashlib
    from app.services.jar_artifact import save_library_jar_bytes

    art = db.query(StreamingJarArtifact).filter(StreamingJarArtifact.id == artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="制品不存在")
    assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    if not file.filename or not file.filename.lower().endswith(".jar"):
        raise HTTPException(status_code=400, detail="只支持 .jar 文件")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    mx = db.query(func.max(StreamingJarVersion.version)).filter(StreamingJarVersion.artifact_id == artifact_id).scalar()
    next_ver = (int(mx) if mx is not None else 0) + 1
    save_library_jar_bytes(art.id, next_ver, content)
    ver = StreamingJarVersion(
        artifact_id=art.id,
        version=next_ver,
        file_name=file.filename,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_key=f"library/{art.id}/v{next_ver}/artifact.jar",
        default_main_class=(default_main_class or "").strip() or None,
        change_note=(change_note or "").strip() or None,
        status="active",
        uploaded_by=current_user.id,
    )
    db.add(ver)
    art.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ver)
    return _serialize_jar_version(db, ver, _username_map(db, [ver.uploaded_by]))


@router.post("/jar-versions/{version_id}/deprecate")
def deprecate_jar_version(version_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ver = db.query(StreamingJarVersion).filter(StreamingJarVersion.id == version_id).first()
    if not ver:
        raise HTTPException(status_code=404, detail="版本不存在")
    art = db.query(StreamingJarArtifact).filter(StreamingJarArtifact.id == ver.artifact_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="制品不存在")
    assert_workspace_data_capability(db, current_user, art.workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    refs = db.query(StreamingJob).filter(StreamingJob.jar_version_id == version_id).count()
    refs += (
        db.query(StreamingJobRelease)
        .filter(
            StreamingJobRelease.jar_version_id == version_id,
            StreamingJobRelease.approval_status.in_(("pending", "approved")),
        )
        .count()
    )
    if refs:
        raise HTTPException(
            status_code=409,
            detail=f"仍有 {refs} 个作业或待用发布版本引用该版本，不能废弃",
        )
    ver.status = "deprecated"
    art.updated_at = datetime.utcnow()
    db.commit()
    return _serialize_jar_version(db, ver, _username_map(db, [ver.uploaded_by]))


def backfill_job_jars_into_library(db: Session) -> int:
    """将仍只有 jar_path / 本地制品的作业幂等回填到制品库。"""
    from app.services.jar_artifact import jar_artifact_exists, artifact_file_path, save_library_jar_bytes
    import hashlib

    n = 0
    jobs = (
        db.query(StreamingJob)
        .filter(StreamingJob.job_type == "JAR", StreamingJob.jar_version_id.is_(None))
        .all()
    )
    for job in jobs:
        if not jar_artifact_exists(job.id):
            continue
        path = artifact_file_path(job.id)
        if not path.is_file():
            continue
        content = path.read_bytes()
        name = (job.name or f"job-{job.id}").strip()[:120] or f"job-{job.id}"
        art = StreamingJarArtifact(
            workspace_id=job.workspace_id,
            name=f"{name}-migrated",
            description=f"从作业 {job.name}(#{job.id}) 本地制品迁移",
            owner_id=job.owner_id or job.created_by,
            created_by=job.created_by,
        )
        db.add(art)
        db.flush()
        save_library_jar_bytes(art.id, 1, content)
        ver = StreamingJarVersion(
            artifact_id=art.id,
            version=1,
            file_name=job.jar_path or "artifact.jar",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=f"library/{art.id}/v1/artifact.jar",
            default_main_class=job.main_class,
            change_note="自动迁移自作业本地 JAR",
            status="active",
            uploaded_by=job.created_by,
        )
        db.add(ver)
        db.flush()
        job.jar_artifact_id = art.id
        job.jar_version_id = ver.id
        n += 1
    if n:
        db.commit()
    return n


@router.post("/jar-artifacts/backfill")
def backfill_jar_artifacts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_STREAM_WRITE)
    # 仅回填该空间
    from app.services.jar_artifact import jar_artifact_exists, artifact_file_path, save_library_jar_bytes
    import hashlib

    n = 0
    jobs = (
        db.query(StreamingJob)
        .filter(
            StreamingJob.workspace_id == workspace_id,
            StreamingJob.job_type == "JAR",
            StreamingJob.jar_version_id.is_(None),
        )
        .all()
    )
    for job in jobs:
        if not jar_artifact_exists(job.id):
            continue
        path = artifact_file_path(job.id)
        if not path.is_file():
            continue
        content = path.read_bytes()
        name = (job.name or f"job-{job.id}").strip()[:120] or f"job-{job.id}"
        art = StreamingJarArtifact(
            workspace_id=job.workspace_id,
            name=f"{name}-migrated",
            description=f"从作业 {job.name}(#{job.id}) 本地制品迁移",
            owner_id=job.owner_id or job.created_by,
            created_by=current_user.id,
        )
        db.add(art)
        db.flush()
        save_library_jar_bytes(art.id, 1, content)
        ver = StreamingJarVersion(
            artifact_id=art.id,
            version=1,
            file_name=job.jar_path or "artifact.jar",
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=f"library/{art.id}/v1/artifact.jar",
            default_main_class=job.main_class,
            change_note="自动迁移自作业本地 JAR",
            status="active",
            uploaded_by=current_user.id,
        )
        db.add(ver)
        db.flush()
        job.jar_artifact_id = art.id
        job.jar_version_id = ver.id
        n += 1
    if n:
        db.commit()
    return {"migrated": n}


@router.get("/jobs/{job_id}/artifact.jar")
def download_jar_artifact(job_id: int, token: str = Query(..., description="与 FLINK_OPERATOR_ARTIFACT_TOKEN 一致")):
    """供 Flink Operator Pod HTTP 拉取 JAR（无 JWT；校验 artifact token）。"""
    from app.services.jar_artifact import ensure_artifact_file, artifact_download_token_is_valid

    if not artifact_download_token_is_valid(token):
        raise HTTPException(status_code=403, detail="无效 artifact token")
    path = ensure_artifact_file(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="JAR 制品不存在")
    return FileResponse(path, media_type="application/java-archive", filename="artifact.jar")


@router.get("/jobs/{job_id}/artifact.sql")
def download_sql_artifact(job_id: int, token: str = Query(..., description="与 FLINK_OPERATOR_ARTIFACT_TOKEN 一致")):
    """供调试/备用：SQL Operator 主路径为 ConfigMap 挂载；此端点供 HTTP 拉取场景。"""
    from app.services.jar_artifact import artifact_download_token_is_valid
    from app.services.sql_artifact import ensure_sql_script_file

    if not artifact_download_token_is_valid(token):
        raise HTTPException(status_code=403, detail="无效 artifact token")
    path = ensure_sql_script_file(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="SQL 制品不存在")
    return FileResponse(path, media_type="text/plain; charset=utf-8", filename="artifact.sql")


@router.post("/jobs/{job_id}/upload-jar")
async def upload_jar(
    job_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db_flink),
    current_user: User = Depends(get_current_user),
):
    """上传 JAR：写入制品库；若已配置 Session JM 则同步上传到 Flink（开发试跑）。"""
    from app.services.jar_artifact import save_jar_bytes

    job = require_streaming_job(db, current_user, job_id, "developer", PC.GIDO_STREAM_WRITE)
    if getattr(job, "is_locked", False):
        raise HTTPException(status_code=403, detail="作业已锁定，请先解锁后再上传 JAR")
    if not file.filename.endswith(".jar"):
        raise HTTPException(status_code=400, detail="只支持 .jar 文件")
    try:
        content = await file.read()
        save_jar_bytes(job.id, content)
        from app.services.jar_artifact import build_jar_s3_uri_for_operator

        s3_uri = build_jar_s3_uri_for_operator(job.id)
        jar_name = file.filename
        jar_mode = _normalize_jar_submit_mode(getattr(job, "flink_jar_submit_mode", None))
        session_jar_id = None
        if jar_mode == "session":
            try:
                session_jar_id = _flink_client_for_job(db, job).upload_jar(content, file.filename)
                jar_name = session_jar_id.split("/")[-1] if "/" in session_jar_id else session_jar_id
            except Exception as ex:
                logger.warning("Session JM 上传 JAR 失败（制品已保存）: %s", ex)
        job.jar_path = jar_name
        db.commit()
        return {
            "message": "上传成功",
            "jar_id": jar_name,
            "filename": file.filename,
            "artifact_saved": True,
            "session_uploaded": bool(session_jar_id),
            "s3_uri": s3_uri,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {e}")


def _merge_connector_pipeline_jars(db: Session, job: StreamingJob, flink_extra: Optional[dict]) -> dict:
    """将作业绑定的连接器版本解析为 URI，写入 pipeline.jars（分号分隔）。"""
    from app.services.artifact_s3 import JAR_ARTIFACT_FILENAME
    from app.services.jar_artifact import resolve_connector_uri_for_operator

    out = dict(flink_extra or {})
    ids = _parse_version_id_list(getattr(job, "connector_version_ids", None))
    if not ids:
        return out
    uris: List[str] = []
    for vid in ids:
        ver = db.query(StreamingConnectorVersion).filter(StreamingConnectorVersion.id == vid).first()
        if not ver or ver.status != "active":
            raise HTTPException(status_code=400, detail=f"连接器版本 {vid} 不可用")
        art = db.query(StreamingConnectorArtifact).filter(StreamingConnectorArtifact.id == ver.artifact_id).first()
        if not art or art.workspace_id != job.workspace_id:
            raise HTTPException(status_code=400, detail=f"连接器版本 {vid} 不属于当前工作空间")
        uris.append(
            resolve_connector_uri_for_operator(
                int(ver.id),
                int(ver.artifact_id),
                int(ver.version),
                JAR_ARTIFACT_FILENAME,
            )
        )
    if not uris:
        return out
    existing = (out.get("pipeline.jars") or "").strip()
    merged = ";".join([p for p in ([existing] if existing else []) + uris if p])
    out["pipeline.jars"] = merged
    return out


from app.api.streaming_resource_library import register_routes as _register_resource_library_routes

_register_resource_library_routes(router)

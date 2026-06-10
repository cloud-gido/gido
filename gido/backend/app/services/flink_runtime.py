# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""Flink 运行时配置：数据库覆盖项 + 环境变量回退（与 Dolphin 集成同模式）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.ds_runtime import (
    _get_global_row,
    _get_workspace_row,
    ensure_platform_integration_row,
    resolve_ds_url_for_backend_http,
)


@dataclass
class FlinkRuntimeConfig:
    flink_url: str
    flink_sql_gateway_url: str
    flink_gateway_jobmanager_rest_url: Optional[str]
    flink_ui_url: Optional[str]
    flink_k8s_application_image: str
    flink_k8s_namespace: Optional[str]
    flink_k8s_application_jm_rest_template: Optional[str]
    # 集群内 SQL Gateway / K8s Application 与清单对齐（可插拔）
    flink_k8s_cluster_domain: str
    flink_k8s_apiserver_fallback_url: Optional[str]
    flink_k8s_jm_rpc_host: Optional[str]
    flink_k8s_sql_gateway_rest_host: Optional[str]


def _str_or_none(v: Optional[object]) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def flink_runtime_from_env_only() -> FlinkRuntimeConfig:
    """仅环境变量 / settings，不含库内平台集成与 Session Profile 覆盖。"""
    jm = (settings.FLINK_URL or "").strip()
    gw = (settings.FLINK_SQL_GATEWAY_URL or "").strip()
    gwjm = (settings.FLINK_GATEWAY_JOBMANAGER_REST_URL or "").strip() or None
    ui = (settings.FLINK_UI_URL or "").strip() or None
    k8s_img = (settings.FLINK_K8S_APPLICATION_IMAGE or "").strip()
    k8s_ns = _str_or_none(settings.FLINK_K8S_NAMESPACE)
    k8s_tpl = _str_or_none(settings.FLINK_K8S_APPLICATION_JM_REST_TEMPLATE)
    k8s_domain = (settings.FLINK_K8S_CLUSTER_DOMAIN or "cluster.local").strip().rstrip(".") or "cluster.local"
    k8s_fb = _str_or_none(settings.FLINK_K8S_APISERVER_FALLBACK_URL)
    k8s_jm_rpc = _str_or_none(settings.FLINK_K8S_JM_RPC_HOST)
    k8s_gw_rest = _str_or_none(settings.FLINK_K8S_SQL_GATEWAY_REST_HOST)
    return FlinkRuntimeConfig(
        flink_url=jm,
        flink_sql_gateway_url=gw,
        flink_gateway_jobmanager_rest_url=gwjm,
        flink_ui_url=ui,
        flink_k8s_application_image=k8s_img,
        flink_k8s_namespace=k8s_ns,
        flink_k8s_application_jm_rest_template=k8s_tpl,
        flink_k8s_cluster_domain=k8s_domain,
        flink_k8s_apiserver_fallback_url=k8s_fb,
        flink_k8s_jm_rpc_host=k8s_jm_rpc,
        flink_k8s_sql_gateway_rest_host=k8s_gw_rest,
    )


def apply_flink_row_overrides(cfg: FlinkRuntimeConfig, row: Optional[object]) -> FlinkRuntimeConfig:
    """将单行库表（平台集成或 Session Profile）上的 Flink 列覆盖到 cfg；row 为 None 则原样返回。"""
    if row is None:
        return cfg
    jm = cfg.flink_url
    gw = cfg.flink_sql_gateway_url
    gwjm = cfg.flink_gateway_jobmanager_rest_url
    ui = cfg.flink_ui_url
    k8s_img = cfg.flink_k8s_application_image
    k8s_ns = cfg.flink_k8s_namespace
    k8s_tpl = cfg.flink_k8s_application_jm_rest_template
    k8s_domain = cfg.flink_k8s_cluster_domain
    k8s_fb = cfg.flink_k8s_apiserver_fallback_url
    k8s_jm_rpc = cfg.flink_k8s_jm_rpc_host
    k8s_gw_rest = cfg.flink_k8s_sql_gateway_rest_host

    if getattr(row, "flink_url", None) and str(row.flink_url).strip():
        jm = str(row.flink_url).strip()
    if getattr(row, "flink_sql_gateway_url", None) and str(row.flink_sql_gateway_url).strip():
        gw = str(row.flink_sql_gateway_url).strip()
    gwjm_attr = getattr(row, "flink_gateway_jobmanager_rest_url", None)
    if gwjm_attr is not None and str(gwjm_attr).strip():
        gwjm = str(gwjm_attr).strip()
    elif gwjm_attr is not None and not str(gwjm_attr).strip():
        gwjm = None
    ui_attr = getattr(row, "flink_ui_url", None)
    if ui_attr is not None:
        u = str(ui_attr).strip()
        ui = u if u else None

    if getattr(row, "flink_k8s_application_image", None) and str(row.flink_k8s_application_image).strip():
        k8s_img = str(row.flink_k8s_application_image).strip()
    if getattr(row, "flink_k8s_namespace", None) is not None:
        ns = str(row.flink_k8s_namespace or "").strip()
        k8s_ns = ns if ns else None
    if getattr(row, "flink_k8s_application_jm_rest_template", None) is not None:
        t = str(row.flink_k8s_application_jm_rest_template or "").strip()
        k8s_tpl = t if t else None

    if getattr(row, "flink_k8s_cluster_domain", None) is not None:
        d = str(row.flink_k8s_cluster_domain or "").strip().rstrip(".")
        k8s_domain = d if d else "cluster.local"
    if getattr(row, "flink_k8s_apiserver_fallback_url", None) is not None:
        f = str(row.flink_k8s_apiserver_fallback_url or "").strip()
        k8s_fb = f if f else None
    if getattr(row, "flink_k8s_jm_rpc_host", None) is not None:
        h = str(row.flink_k8s_jm_rpc_host or "").strip()
        k8s_jm_rpc = h if h else None
    if getattr(row, "flink_k8s_sql_gateway_rest_host", None) is not None:
        h = str(row.flink_k8s_sql_gateway_rest_host or "").strip()
        k8s_gw_rest = h if h else None

    return FlinkRuntimeConfig(
        flink_url=jm,
        flink_sql_gateway_url=gw,
        flink_gateway_jobmanager_rest_url=gwjm,
        flink_ui_url=ui,
        flink_k8s_application_image=k8s_img,
        flink_k8s_namespace=k8s_ns,
        flink_k8s_application_jm_rest_template=k8s_tpl,
        flink_k8s_cluster_domain=k8s_domain,
        flink_k8s_apiserver_fallback_url=k8s_fb,
        flink_k8s_jm_rpc_host=k8s_jm_rpc,
        flink_k8s_sql_gateway_rest_host=k8s_gw_rest,
    )


def get_flink_runtime(db: Session, workspace_id: Optional[int] = None) -> FlinkRuntimeConfig:
    base = flink_runtime_from_env_only()
    global_row = _get_global_row(db) or ensure_platform_integration_row(db)
    merged = apply_flink_row_overrides(base, global_row)
    if workspace_id is not None:
        ws_row = _get_workspace_row(db, int(workspace_id))
        merged = apply_flink_row_overrides(merged, ws_row)
    return merged


def get_flink_runtime_for_workspace_profile(
    db: Session, workspace_id: int, profile_id: Optional[int]
) -> FlinkRuntimeConfig:
    """
    实时作业选用的 Session 配置：在「环境 + 空间/全局平台集成」之上再叠一层 Profile；
    profile_id 为空或记录不存在 / 空间不匹配时退化为 get_flink_runtime(db, workspace_id)。
    """
    merged = get_flink_runtime(db, workspace_id)
    if not profile_id:
        return merged
    from app.models.workspace import FlinkSessionProfile

    prof = (
        db.query(FlinkSessionProfile)
        .filter(FlinkSessionProfile.id == int(profile_id), FlinkSessionProfile.workspace_id == int(workspace_id))
        .first()
    )
    if not prof:
        return merged
    return apply_flink_row_overrides(merged, prof)


def resolved_flink_k8s_namespace(cfg: FlinkRuntimeConfig, default: str = "flink") -> str:
    s = (cfg.flink_k8s_namespace or default).strip()
    return s or default


def resolved_flink_k8s_cluster_domain(cfg: FlinkRuntimeConfig) -> str:
    d = (cfg.flink_k8s_cluster_domain or "cluster.local").strip().rstrip(".")
    return d or "cluster.local"


def resolved_flink_k8s_apiserver_fallback(cfg: FlinkRuntimeConfig) -> str:
    if (cfg.flink_k8s_apiserver_fallback_url or "").strip():
        return cfg.flink_k8s_apiserver_fallback_url.strip()
    d = resolved_flink_k8s_cluster_domain(cfg)
    return f"https://kubernetes.default.svc.{d}:443"


def resolved_flink_k8s_jm_rpc_host(cfg: FlinkRuntimeConfig) -> str:
    if (cfg.flink_k8s_jm_rpc_host or "").strip():
        return cfg.flink_k8s_jm_rpc_host.strip()
    ns = resolved_flink_k8s_namespace(cfg)
    d = resolved_flink_k8s_cluster_domain(cfg)
    return f"flink-jobmanager.{ns}.svc.{d}"


def resolved_flink_k8s_sql_gateway_rest_host(cfg: FlinkRuntimeConfig) -> str:
    if (cfg.flink_k8s_sql_gateway_rest_host or "").strip():
        return cfg.flink_k8s_sql_gateway_rest_host.strip()
    ns = resolved_flink_k8s_namespace(cfg)
    d = resolved_flink_k8s_cluster_domain(cfg)
    return f"flink-sql-gateway.{ns}.svc.{d}"


def refresh_flink_client(db: Session, workspace_id: Optional[int] = None) -> None:
    """将全局 Flink 客户端同步为当前库内有效配置（调用 Flink REST 前应执行）。"""
    cfg = get_flink_runtime(db, workspace_id)
    from app.api.streaming import flink as flink_client

    # UI 基底仅给浏览器打开（flink_console_url），不得经过 resolve_ds_url_for_backend_http：
    # 后者在容器内会把 localhost→host.docker.internal，导致用户点开链接时出现 502/不可达。
    ui_for_browser = (cfg.flink_ui_url or "").strip() or None

    flink_client.apply_runtime(
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

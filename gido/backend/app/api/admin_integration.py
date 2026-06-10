# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""管理员配置 DolphinScheduler、Flink 等集成项（库覆盖环境变量，可插拔对接外部集群）。"""
from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.access import require_platform_manager
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.ds_runtime import ensure_platform_integration_row, get_dolphin_runtime, refresh_ds_client
from app.services.flink_runtime import (
    get_flink_runtime,
    refresh_flink_client,
    resolved_flink_k8s_apiserver_fallback,
    resolved_flink_k8s_jm_rpc_host,
    resolved_flink_k8s_sql_gateway_rest_host,
)
from app.services.flink_sql_gateway_k8s_yml import render_flink_sql_gateway_deployment_yaml

router = APIRouter(prefix="/admin/integration", tags=["系统管理-集成"])


def _mask_token(t: Optional[str]) -> Optional[str]:
    if not t or not str(t).strip():
        return None
    s = str(t).strip()
    if len(s) <= 6:
        return "****"
    return "****" + s[-4:]


class DolphinIntegrationOut(BaseModel):
    """当前生效值 + 库中覆盖项（便于 UI）"""

    effective_enabled: bool
    effective_url: str
    effective_ui_url: Optional[str] = None
    effective_project_name: str
    effective_token_configured: bool
    # effective_url 来自库覆盖还是环境变量（便于排查「不填表单仍失败」）
    effective_url_source: str  # "environment" | "database"

    override_enabled: Optional[bool] = None
    override_url: Optional[str] = None
    override_ui_url: Optional[str] = None
    override_project_name: Optional[str] = None
    token_configured_in_db: bool
    token_masked: Optional[str] = None

    env_ds_enabled: bool
    env_ds_url: str


class DolphinIntegrationUpdate(BaseModel):
    """写入 id=1 行；字段为 null 表示该项继续沿用环境变量（不修改库中该列可用专门接口或留空逻辑）。"""

    ds_enabled: Optional[bool] = None
    ds_url: Optional[str] = None
    ds_ui_url: Optional[str] = None
    ds_project_name: Optional[str] = None
    # None = 不修改库中 token；"" = 清空库中 token（回退环境变量）；非空 = 更新
    ds_token: Optional[str] = Field(default=None, description="不传则不更新；空字符串清空库中 token")


def _dolphin_integration_out(db: Session) -> DolphinIntegrationOut:
    from app.core.config import settings

    row = ensure_platform_integration_row(db)
    eff = get_dolphin_runtime(db)
    url_from_db = bool(row.ds_url and str(row.ds_url).strip())
    return DolphinIntegrationOut(
        effective_enabled=eff.enabled,
        effective_url=eff.url,
        effective_ui_url=eff.ui_url,
        effective_project_name=eff.project_name,
        effective_token_configured=bool(eff.token),
        effective_url_source="database" if url_from_db else "environment",
        override_enabled=row.ds_enabled,
        override_url=row.ds_url,
        override_ui_url=row.ds_ui_url,
        override_project_name=row.ds_project_name,
        token_configured_in_db=bool(row.ds_token and row.ds_token.strip()),
        token_masked=_mask_token(row.ds_token),
        env_ds_enabled=settings.DS_ENABLED,
        env_ds_url=settings.DS_URL,
    )


@router.get("/dolphin", response_model=DolphinIntegrationOut, dependencies=[Depends(require_platform_manager)])
def get_dolphin_integration(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _dolphin_integration_out(db)


@router.put("/dolphin", response_model=DolphinIntegrationOut, dependencies=[Depends(require_platform_manager)])
def put_dolphin_integration(
    body: DolphinIntegrationUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = ensure_platform_integration_row(db)
    data = body.model_dump(exclude_unset=True)
    if "ds_enabled" in data:
        row.ds_enabled = data["ds_enabled"]
    if "ds_url" in data:
        row.ds_url = (data["ds_url"] or "").strip() or None
    if "ds_ui_url" in data:
        v = data["ds_ui_url"]
        row.ds_ui_url = None if v is None else (str(v).strip() or "")
    if "ds_project_name" in data:
        row.ds_project_name = (data["ds_project_name"] or "").strip() or None
    if "ds_token" in data:
        t = data["ds_token"]
        if t is None:
            pass
        elif t == "":
            row.ds_token = None
        else:
            row.ds_token = str(t).strip()
    db.add(row)
    db.commit()
    db.refresh(row)
    refresh_ds_client(db)
    return _dolphin_integration_out(db)


@router.post("/dolphin/test", dependencies=[Depends(require_platform_manager)])
def test_dolphin(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    cfg = get_dolphin_runtime(db)
    if not cfg.enabled:
        raise HTTPException(status_code=400, detail="DolphinScheduler 未启用")
    if not cfg.token:
        raise HTTPException(status_code=400, detail="未配置 Token（库或环境变量 DS_TOKEN）")
    refresh_ds_client(db)
    from app.services.dolphin import ds_client

    try:
        base = ds_client.base
        ds_client._get("/projects", params={"pageNo": 1, "pageSize": 1})
        return {
            "ok": True,
            "message": "连接正常（已请求 /projects）",
            # 与 curl 对比用：须与你在 shell 里访问的 API 根路径一致（不含 token）
            "requested_url": f"{base}/projects?pageNo=1&pageSize=1",
        }
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise HTTPException(
                status_code=502,
                detail=(
                    "鉴权失败 (HTTP 401)：Dolphin API 地址可达，但 Token 无效、已过期或与当前 DS 实例不匹配。"
                    " 请到 Dolphin UI「安全中心 → 令牌管理」新建令牌，将完整 Token 粘贴到「DS API Token」后保存"
                    "（留空不会更新库中旧 Token），再点「测试连接」。"
                    f" 请求: {ds_client.base}/projects?pageNo=1&pageSize=1"
                ),
            )
        msg = str(e)
        hints = []
        if "Connection refused" in msg or "Errno 111" in msg:
            hints.append(
                "连接被拒绝：请确认 DolphinScheduler API 已启动并监听该端口。"
                " 若 GIDO 后端在 Docker 容器内，请把「DS API 根路径」改为访问宿主机，例如 "
                "http://host.docker.internal:12345/dolphinscheduler（Linux 需 compose extra_hosts），不要用 localhost。"
            )
        if "/ui/" in msg or "/ui?" in msg or msg.rstrip("/").endswith("/ui"):
            hints.append(
                "请求路径里出现了 /ui/：API 根路径应填 http://主机:端口/dolphinscheduler，不要带 /ui（浏览器地址请填「DS Web UI」项）。"
            )
        detail = "网络错误: " + msg
        if hints:
            detail += " — " + " ".join(hints)
        raise HTTPException(status_code=502, detail=detail)
    except requests.RequestException as e:
        msg = str(e)
        hints = []
        if "Connection refused" in msg or "Errno 111" in msg:
            hints.append(
                "连接被拒绝：请确认 DolphinScheduler API 已启动并监听该端口。"
                " 若 GIDO 后端在 Docker 容器内，请把「DS API 根路径」改为访问宿主机，例如 "
                "http://host.docker.internal:12345/dolphinscheduler（Linux 需 compose extra_hosts），不要用 localhost。"
            )
        detail = "网络错误: " + msg
        if hints:
            detail += " — " + " ".join(hints)
        raise HTTPException(status_code=502, detail=detail)
    except RuntimeError as e:
        # DS 常返回 HTTP 200 + JSON { code != 0, msg }，与「网络不通」不同
        raise HTTPException(
            status_code=502,
            detail=(
                f"{e} — 当前 API 基址: {ds_client.base}。"
                " 若 curl 也是 200，请对比 curl 是否带相同 token、以及响应 JSON 是否 code=0。"
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/dolphin/reset-overrides", response_model=DolphinIntegrationOut, dependencies=[Depends(require_platform_manager)])
def reset_dolphin_overrides(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """清空库中 Dolphin 覆盖项，全部回退为环境变量。"""
    row = ensure_platform_integration_row(db)
    row.ds_enabled = None
    row.ds_url = None
    row.ds_ui_url = None
    row.ds_token = None
    row.ds_project_name = None
    db.add(row)
    db.commit()
    refresh_ds_client(db)
    return _dolphin_integration_out(db)


# ---------- Flink ----------


class FlinkIntegrationOut(BaseModel):
    effective_flink_url: str
    effective_sql_gateway_url: str
    effective_gateway_jm_rest_url: Optional[str] = None
    effective_ui_url: Optional[str] = None
    effective_flink_k8s_application_image: str
    effective_flink_k8s_namespace: Optional[str] = None
    effective_flink_k8s_application_jm_rest_template: Optional[str] = None
    effective_flink_url_source: str  # "environment" | "database"
    effective_flink_k8s_image_source: str  # "environment" | "database"
    # 集群内 SQL Gateway / K8s：生效推导（与「导出 Deployment YAML」一致）
    effective_flink_k8s_cluster_domain: str
    effective_flink_k8s_apiserver_fallback_url: str
    effective_flink_k8s_jm_rpc_host: str
    effective_flink_k8s_sql_gateway_rest_host: str

    override_flink_url: Optional[str] = None
    override_sql_gateway_url: Optional[str] = None
    override_gateway_jm_rest_url: Optional[str] = None
    override_ui_url: Optional[str] = None
    override_flink_k8s_application_image: Optional[str] = None
    override_flink_k8s_namespace: Optional[str] = None
    override_flink_k8s_application_jm_rest_template: Optional[str] = None
    override_flink_k8s_cluster_domain: Optional[str] = None
    override_flink_k8s_apiserver_fallback_url: Optional[str] = None
    override_flink_k8s_jm_rpc_host: Optional[str] = None
    override_flink_k8s_sql_gateway_rest_host: Optional[str] = None

    env_flink_url: Optional[str] = None
    env_sql_gateway_url: Optional[str] = None
    env_gateway_jm_rest_url: Optional[str] = None
    env_ui_url: Optional[str] = None
    env_flink_k8s_application_image: str
    env_flink_k8s_namespace: Optional[str] = None
    env_flink_k8s_application_jm_rest_template: Optional[str] = None
    env_flink_k8s_cluster_domain: str
    env_flink_k8s_apiserver_fallback_url: Optional[str] = None
    env_flink_k8s_jm_rpc_host: Optional[str] = None
    env_flink_k8s_sql_gateway_rest_host: Optional[str] = None


class FlinkIntegrationUpdate(BaseModel):
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


def _flink_integration_out(db: Session) -> FlinkIntegrationOut:
    from app.core.config import settings

    row = ensure_platform_integration_row(db)
    eff = get_flink_runtime(db)
    jm_from_db = bool(row.flink_url and str(row.flink_url).strip())
    img_from_db = bool(getattr(row, "flink_k8s_application_image", None) and str(row.flink_k8s_application_image).strip())
    return FlinkIntegrationOut(
        effective_flink_url=eff.flink_url,
        effective_sql_gateway_url=eff.flink_sql_gateway_url,
        effective_gateway_jm_rest_url=eff.flink_gateway_jobmanager_rest_url,
        effective_ui_url=eff.flink_ui_url,
        effective_flink_k8s_application_image=eff.flink_k8s_application_image,
        effective_flink_k8s_namespace=eff.flink_k8s_namespace,
        effective_flink_k8s_application_jm_rest_template=eff.flink_k8s_application_jm_rest_template,
        effective_flink_url_source="database" if jm_from_db else "environment",
        effective_flink_k8s_image_source="database" if img_from_db else "environment",
        effective_flink_k8s_cluster_domain=eff.flink_k8s_cluster_domain,
        effective_flink_k8s_apiserver_fallback_url=resolved_flink_k8s_apiserver_fallback(eff),
        effective_flink_k8s_jm_rpc_host=resolved_flink_k8s_jm_rpc_host(eff),
        effective_flink_k8s_sql_gateway_rest_host=resolved_flink_k8s_sql_gateway_rest_host(eff),
        override_flink_url=row.flink_url,
        override_sql_gateway_url=row.flink_sql_gateway_url,
        override_gateway_jm_rest_url=row.flink_gateway_jobmanager_rest_url,
        override_ui_url=row.flink_ui_url,
        override_flink_k8s_application_image=getattr(row, "flink_k8s_application_image", None),
        override_flink_k8s_namespace=getattr(row, "flink_k8s_namespace", None),
        override_flink_k8s_application_jm_rest_template=getattr(row, "flink_k8s_application_jm_rest_template", None),
        override_flink_k8s_cluster_domain=getattr(row, "flink_k8s_cluster_domain", None),
        override_flink_k8s_apiserver_fallback_url=getattr(row, "flink_k8s_apiserver_fallback_url", None),
        override_flink_k8s_jm_rpc_host=getattr(row, "flink_k8s_jm_rpc_host", None),
        override_flink_k8s_sql_gateway_rest_host=getattr(row, "flink_k8s_sql_gateway_rest_host", None),
        env_flink_url=settings.FLINK_URL,
        env_sql_gateway_url=settings.FLINK_SQL_GATEWAY_URL,
        env_gateway_jm_rest_url=settings.FLINK_GATEWAY_JOBMANAGER_REST_URL,
        env_ui_url=settings.FLINK_UI_URL,
        env_flink_k8s_application_image=(settings.FLINK_K8S_APPLICATION_IMAGE or "").strip(),
        env_flink_k8s_namespace=settings.FLINK_K8S_NAMESPACE,
        env_flink_k8s_application_jm_rest_template=settings.FLINK_K8S_APPLICATION_JM_REST_TEMPLATE,
        env_flink_k8s_cluster_domain=(settings.FLINK_K8S_CLUSTER_DOMAIN or "cluster.local").strip(),
        env_flink_k8s_apiserver_fallback_url=settings.FLINK_K8S_APISERVER_FALLBACK_URL,
        env_flink_k8s_jm_rpc_host=settings.FLINK_K8S_JM_RPC_HOST,
        env_flink_k8s_sql_gateway_rest_host=settings.FLINK_K8S_SQL_GATEWAY_REST_HOST,
    )


@router.get("/flink", response_model=FlinkIntegrationOut, dependencies=[Depends(require_platform_manager)])
def get_flink_integration(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _flink_integration_out(db)


@router.get("/flink/sql-gateway-k8s-yml", dependencies=[Depends(require_platform_manager)])
def get_flink_sql_gateway_k8s_yml(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """按当前生效集成配置生成 flink-sql-gateway Deployment YAML（kubectl apply 对接不同 K8s）。"""
    cfg = get_flink_runtime(db)
    body = render_flink_sql_gateway_deployment_yaml(cfg)
    return PlainTextResponse(content=body, media_type="text/yaml; charset=utf-8")


@router.put("/flink", response_model=FlinkIntegrationOut, dependencies=[Depends(require_platform_manager)])
def put_flink_integration(
    body: FlinkIntegrationUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = ensure_platform_integration_row(db)
    data = body.model_dump(exclude_unset=True)
    if "flink_url" in data:
        row.flink_url = (data["flink_url"] or "").strip() or None
    if "flink_sql_gateway_url" in data:
        row.flink_sql_gateway_url = (data["flink_sql_gateway_url"] or "").strip() or None
    if "flink_gateway_jobmanager_rest_url" in data:
        v = data["flink_gateway_jobmanager_rest_url"]
        row.flink_gateway_jobmanager_rest_url = None if v is None else (str(v).strip() or None)
    if "flink_ui_url" in data:
        v = data["flink_ui_url"]
        if v is None:
            row.flink_ui_url = None
        else:
            s = str(v).strip()
            row.flink_ui_url = s if s else None
    if "flink_k8s_application_image" in data:
        row.flink_k8s_application_image = (data["flink_k8s_application_image"] or "").strip() or None
    if "flink_k8s_namespace" in data:
        v = data["flink_k8s_namespace"]
        row.flink_k8s_namespace = None if v is None else (str(v).strip() or None)
    if "flink_k8s_application_jm_rest_template" in data:
        v = data["flink_k8s_application_jm_rest_template"]
        row.flink_k8s_application_jm_rest_template = None if v is None else (str(v).strip() or None)
    if "flink_k8s_cluster_domain" in data:
        v = data["flink_k8s_cluster_domain"]
        if v is None:
            row.flink_k8s_cluster_domain = None
        else:
            s = str(v).strip().rstrip(".")
            row.flink_k8s_cluster_domain = s if s else None
    if "flink_k8s_apiserver_fallback_url" in data:
        v = data["flink_k8s_apiserver_fallback_url"]
        row.flink_k8s_apiserver_fallback_url = None if v is None else (str(v).strip() or None)
    if "flink_k8s_jm_rpc_host" in data:
        v = data["flink_k8s_jm_rpc_host"]
        row.flink_k8s_jm_rpc_host = None if v is None else (str(v).strip() or None)
    if "flink_k8s_sql_gateway_rest_host" in data:
        v = data["flink_k8s_sql_gateway_rest_host"]
        row.flink_k8s_sql_gateway_rest_host = None if v is None else (str(v).strip() or None)
    db.add(row)
    db.commit()
    db.refresh(row)
    refresh_flink_client(db)
    return _flink_integration_out(db)


@router.post("/flink/test", dependencies=[Depends(require_platform_manager)])
def test_flink_integration(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    refresh_flink_client(db)
    from app.api.streaming import flink

    jm_ok, jm_err, overview = False, None, None
    try:
        overview = flink.overview()
        jm_ok = True
    except Exception as e:
        jm_err = str(e)
    gw = flink.gateway_info_probe()
    ok = jm_ok and gw.get("ok")
    return {
        "ok": ok,
        "jobmanager": {"reachable": jm_ok, "error": jm_err, "overview": overview},
        "sql_gateway": gw,
        "effective": {
            "flink_url": flink.jm_base,
            "sql_gateway_url": flink._sql_gateway_url or None,
            "gateway_jm_rest_url": flink._gateway_jm_rest_url,
            "ui_url": flink._ui_url,
        },
    }


@router.post("/flink/deploy-hint", dependencies=[Depends(require_platform_manager)])
def flink_deploy_hint(db: Session = Depends(get_db), _: User = Depends(get_current_user)) -> Dict[str, Any]:
    """根据当前生效配置生成可粘贴的 compose / .env 片段（不执行远程安装）。"""
    cfg = get_flink_runtime(db)
    lines = [
        f"GIDO_FLINK_URL={cfg.flink_url}",
        f"GIDO_FLINK_SQL_GATEWAY_URL={cfg.flink_sql_gateway_url}",
    ]
    if cfg.flink_gateway_jobmanager_rest_url:
        lines.append(f"GIDO_FLINK_GATEWAY_JOBMANAGER_REST_URL={cfg.flink_gateway_jobmanager_rest_url}")
    if cfg.flink_ui_url:
        lines.append(f"GIDO_FLINK_UI_URL={cfg.flink_ui_url}")
    if cfg.flink_k8s_application_image:
        lines.append(f"GIDO_FLINK_K8S_APPLICATION_IMAGE={cfg.flink_k8s_application_image}")
    if cfg.flink_k8s_namespace:
        lines.append(f"GIDO_FLINK_K8S_NAMESPACE={cfg.flink_k8s_namespace}")
    if cfg.flink_k8s_application_jm_rest_template:
        lines.append(f"GIDO_FLINK_K8S_APPLICATION_JM_REST_TEMPLATE={cfg.flink_k8s_application_jm_rest_template}")
    lines.append(f"GIDO_FLINK_K8S_CLUSTER_DOMAIN={cfg.flink_k8s_cluster_domain}")
    if cfg.flink_k8s_apiserver_fallback_url:
        lines.append(f"GIDO_FLINK_K8S_APISERVER_FALLBACK_URL={cfg.flink_k8s_apiserver_fallback_url}")
    if cfg.flink_k8s_jm_rpc_host:
        lines.append(f"GIDO_FLINK_K8S_JM_RPC_HOST={cfg.flink_k8s_jm_rpc_host}")
    if cfg.flink_k8s_sql_gateway_rest_host:
        lines.append(f"GIDO_FLINK_K8S_SQL_GATEWAY_REST_HOST={cfg.flink_k8s_sql_gateway_rest_host}")
    compose = "environment:\n      - " + "\n      - ".join(lines)
    return {
        "note": (
            "GIDO 不会在远端集群自动安装 Flink；请用 kubectl / Helm / Operator 部署 Session（见仓库根 k8s/legacy/flink.yaml），"
            "再在「系统管理 → 集成」填写 JM / SQL Gateway 与 K8s Application 相关项。"
            " 对接非标准集群域或命名空间时，可填下方「K8s / SQL Gateway」后点「导出 SQL Gateway Deployment YAML」生成与生效配置一致的清单。"
            " 下方变量与「当前生效」一致，可粘贴到 K8s Secret、CI 或 gido/docker-compose.yml。"
        ),
        "env_lines": lines,
        "compose_snippet": compose,
    }


@router.post("/flink/reset-overrides", response_model=FlinkIntegrationOut, dependencies=[Depends(require_platform_manager)])
def reset_flink_overrides(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    row = ensure_platform_integration_row(db)
    row.flink_url = None
    row.flink_sql_gateway_url = None
    row.flink_gateway_jobmanager_rest_url = None
    row.flink_ui_url = None
    row.flink_k8s_application_image = None
    row.flink_k8s_namespace = None
    row.flink_k8s_application_jm_rest_template = None
    row.flink_k8s_cluster_domain = None
    row.flink_k8s_apiserver_fallback_url = None
    row.flink_k8s_jm_rpc_host = None
    row.flink_k8s_sql_gateway_rest_host = None
    db.add(row)
    db.commit()
    refresh_flink_client(db)
    return _flink_integration_out(db)

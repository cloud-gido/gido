# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作空间设置：默认数据源、数仓、按空间的 Dolphin/Flink 集成。"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User, Workspace
from app.services.ds_runtime import (
    ensure_workspace_platform_integration_row,
    get_dolphin_runtime,
    refresh_ds_client,
)
from app.services.flink_runtime import get_flink_runtime, refresh_flink_client
from app.services.rbac import assert_can_edit_workspace_metadata, assert_workspace_access
from app.services.workspace_settings import (
    ensure_workspace_platform_row,
    get_workspace_defaults,
    update_workspace_defaults,
)

router = APIRouter(prefix="/workspaces", tags=["工作空间设置"])


def _mask_token(t: Optional[str]) -> Optional[str]:
    if not t or not str(t).strip():
        return None
    s = str(t).strip()
    if len(s) <= 6:
        return "****"
    return "****" + s[-4:]


class WorkspaceDefaultsOut(BaseModel):
    default_datasource_id: Optional[int] = None
    warehouse_datasource_id: Optional[int] = None
    effective_warehouse_datasource_id: Optional[int] = None


class WorkspaceDefaultsUpdate(BaseModel):
    default_datasource_id: Optional[int] = None
    warehouse_datasource_id: Optional[int] = None
    clear_default_datasource: bool = False
    clear_warehouse_datasource: bool = False


class WorkspaceDolphinOut(BaseModel):
    effective_enabled: bool
    effective_url: str
    effective_ui_url: Optional[str] = None
    effective_project_name: str
    effective_token_configured: bool
    effective_url_source: str  # workspace | global | environment

    override_enabled: Optional[bool] = None
    override_url: Optional[str] = None
    override_ui_url: Optional[str] = None
    override_project_name: Optional[str] = None
    token_configured_in_db: bool
    token_masked: Optional[str] = None

    env_ds_enabled: bool
    env_ds_url: str


class WorkspaceDolphinUpdate(BaseModel):
    ds_enabled: Optional[bool] = None
    ds_url: Optional[str] = None
    ds_ui_url: Optional[str] = None
    ds_project_name: Optional[str] = None
    ds_token: Optional[str] = Field(default=None, description="不传不更新；空串清空")


class WorkspaceFlinkOut(BaseModel):
    effective: Dict[str, Any]
    override: Dict[str, Any]
    env_snapshot: Dict[str, Any]


class WorkspaceFlinkUpdate(BaseModel):
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


def _dolphin_out(db: Session, ws_id: int) -> WorkspaceDolphinOut:
    row = ensure_workspace_platform_row(db, ws_id)
    eff = get_dolphin_runtime(db, ws_id)
    url_from_ws = bool(row.ds_url and str(row.ds_url).strip())
    url_from_global = False
    if not url_from_ws:
        from app.services.ds_runtime import _get_global_row

        g = _get_global_row(db)
        url_from_global = bool(g and g.ds_url and str(g.ds_url).strip())
    if url_from_ws:
        source = "workspace"
    elif url_from_global:
        source = "global"
    else:
        source = "environment"
    return WorkspaceDolphinOut(
        effective_enabled=eff.enabled,
        effective_url=eff.url,
        effective_ui_url=eff.ui_url,
        effective_project_name=eff.project_name,
        effective_token_configured=bool(eff.token),
        effective_url_source=source,
        override_enabled=row.ds_enabled,
        override_url=row.ds_url,
        override_ui_url=row.ds_ui_url,
        override_project_name=row.ds_project_name,
        token_configured_in_db=bool(row.ds_token and row.ds_token.strip()),
        token_masked=_mask_token(row.ds_token),
        env_ds_enabled=settings.DS_ENABLED,
        env_ds_url=settings.DS_URL,
    )


def _flink_field_names():
    return [
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
    ]


def _flink_out(db: Session, ws_id: int) -> WorkspaceFlinkOut:
    row = ensure_workspace_platform_row(db, ws_id)
    eff = get_flink_runtime(db, ws_id)
    names = _flink_field_names()
    effective = {n: getattr(eff, n, None) for n in names}
    override = {n: getattr(row, n, None) for n in names}
    env_snapshot = {
        "flink_url": settings.FLINK_URL,
        "flink_sql_gateway_url": settings.FLINK_SQL_GATEWAY_URL,
        "flink_k8s_namespace": settings.FLINK_K8S_NAMESPACE,
    }
    return WorkspaceFlinkOut(effective=effective, override=override, env_snapshot=env_snapshot)


@router.get("/{ws_id}/settings/defaults", response_model=WorkspaceDefaultsOut)
def get_defaults(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_access(db, current_user, ws_id)
    return WorkspaceDefaultsOut(**get_workspace_defaults(db, ws_id))


@router.put("/{ws_id}/settings/defaults", response_model=WorkspaceDefaultsOut)
def put_defaults(
    ws_id: int,
    body: WorkspaceDefaultsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_can_edit_workspace_metadata(db, current_user, ws_id)
    update_workspace_defaults(
        db,
        ws_id,
        default_datasource_id=body.default_datasource_id,
        warehouse_datasource_id=body.warehouse_datasource_id,
        clear_default=body.clear_default_datasource,
        clear_warehouse=body.clear_warehouse_datasource,
    )
    return WorkspaceDefaultsOut(**get_workspace_defaults(db, ws_id))


@router.get("/{ws_id}/settings/dolphin", response_model=WorkspaceDolphinOut)
def get_ws_dolphin(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_access(db, current_user, ws_id)
    return _dolphin_out(db, ws_id)


@router.put("/{ws_id}/settings/dolphin", response_model=WorkspaceDolphinOut)
def put_ws_dolphin(
    ws_id: int,
    body: WorkspaceDolphinUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_can_edit_workspace_metadata(db, current_user, ws_id)
    row = ensure_workspace_platform_row(db, ws_id)
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
    db.commit()
    refresh_ds_client(db, ws_id)
    return _dolphin_out(db, ws_id)


@router.post("/{ws_id}/settings/dolphin/test")
def test_ws_dolphin(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_can_edit_workspace_metadata(db, current_user, ws_id)
    refresh_ds_client(db, ws_id)
    cfg = get_dolphin_runtime(db, ws_id)
    if not cfg.enabled:
        return {"ok": False, "message": "当前空间未启用 Dolphin"}
    if not cfg.url or not cfg.token:
        return {"ok": False, "message": "请配置 API 地址与 Token"}
    import requests

    try:
        r = requests.get(
            f"{cfg.url.rstrip('/')}/projects/list",
            headers={"token": cfg.token},
            timeout=15,
        )
        if r.status_code == 200:
            return {"ok": True, "message": "连接成功"}
        return {"ok": False, "message": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@router.get("/{ws_id}/settings/flink", response_model=WorkspaceFlinkOut)
def get_ws_flink(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_access(db, current_user, ws_id)
    return _flink_out(db, ws_id)


@router.put("/{ws_id}/settings/flink", response_model=WorkspaceFlinkOut)
def put_ws_flink(
    ws_id: int,
    body: WorkspaceFlinkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_can_edit_workspace_metadata(db, current_user, ws_id)
    row = ensure_workspace_platform_row(db, ws_id)
    for key, val in body.model_dump(exclude_unset=True).items():
        if val is None:
            setattr(row, key, None)
        else:
            s = str(val).strip()
            setattr(row, key, s if s else None)
    db.commit()
    refresh_flink_client(db, ws_id)
    return _flink_out(db, ws_id)

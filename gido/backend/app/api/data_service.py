# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务：API 管理、应用授权、测试、监控、OpenAPI 导出。"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.data_service import (
    ConsumerApp,
    ConsumerAppApiGrant,
    DataApi,
    DataApiInvocationLog,
    DataApiParam,
)
from app.models.workspace import DataSource, User
from app.services.data_api_engine import (
    bind_params,
    execute_data_api,
    generate_app_credentials,
    new_trace_id,
    wizard_to_sql,
)
from app.services.rbac import assert_workspace_data_capability, require_datasource_row
from app.services.publish_approval import assert_can_publish_production
from app.services.data_service_publish import execute_data_api_offline, execute_data_api_publish

router = APIRouter(prefix="/data-service", tags=["数据服务"])
logger = logging.getLogger(__name__)

_API_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


class ApiParamIn(BaseModel):
    name: str
    param_in: str = "query"
    data_type: str = "string"
    required: bool = False
    default_value: Optional[str] = None
    description: Optional[str] = None
    validator_regex: Optional[str] = None
    sort_order: int = 0


class DataApiCreateIn(BaseModel):
    workspace_id: int
    api_code: str
    name: str
    description: Optional[str] = None
    mode: str = "sql"
    http_method: str = "GET"
    datasource_id: Optional[int] = None
    sql_template: Optional[str] = None
    wizard_config: Optional[dict] = None
    response_fields: Optional[List[dict]] = None
    pagination_enabled: bool = True
    page_size_default: int = Field(default=20, ge=1, le=1000)
    page_size_max: int = Field(default=1000, ge=1, le=10000)
    timeout_seconds: int = Field(default=30, ge=3, le=120)
    cache_ttl_seconds: int = Field(default=0, ge=0, le=3600)
    max_rows: int = Field(default=10000, ge=1, le=50000)
    params: List[ApiParamIn] = []


class DataApiUpdateIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    http_method: Optional[str] = None
    datasource_id: Optional[int] = None
    sql_template: Optional[str] = None
    wizard_config: Optional[dict] = None
    response_fields: Optional[List[dict]] = None
    pagination_enabled: Optional[bool] = None
    page_size_default: Optional[int] = Field(default=None, ge=1, le=1000)
    page_size_max: Optional[int] = Field(default=None, ge=1, le=10000)
    timeout_seconds: Optional[int] = Field(default=None, ge=3, le=120)
    cache_ttl_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    max_rows: Optional[int] = Field(default=None, ge=1, le=50000)
    params: Optional[List[ApiParamIn]] = None


class ApiTestIn(BaseModel):
    params: Dict[str, Any] = {}
    page_no: int = Field(default=1, ge=1)
    page_size: Optional[int] = Field(default=None, ge=1, le=10000)


class ConsumerAppCreateIn(BaseModel):
    workspace_id: int
    name: str
    description: Optional[str] = None
    ip_whitelist: Optional[List[str]] = None
    qps_limit: int = Field(default=100, ge=0, le=100000)
    daily_quota: int = Field(default=100000, ge=0, le=100000000)


class GrantIn(BaseModel):
    api_ids: List[int]
    qps_limit: Optional[int] = Field(default=None, ge=0, le=100000)


def _require_api(db: Session, api_id: int, user: User) -> DataApi:
    api = db.query(DataApi).options(joinedload(DataApi.params)).filter(DataApi.id == api_id).first()
    if not api:
        raise HTTPException(status_code=404, detail="API 不存在")
    assert_workspace_data_capability(db, user, api.workspace_id, "viewer", PC.GIDO_SERVICE_READ)
    return api


def _api_out(api: DataApi, ds_name: Optional[str] = None) -> dict:
    return {
        "id": api.id,
        "workspace_id": api.workspace_id,
        "api_code": api.api_code,
        "name": api.name,
        "description": api.description,
        "mode": api.mode,
        "http_method": api.http_method,
        "status": api.status,
        "version": api.version,
        "datasource_id": api.datasource_id,
        "datasource_name": ds_name,
        "sql_template": api.sql_template,
        "wizard_config": api.wizard_config,
        "response_fields": api.response_fields,
        "pagination_enabled": api.pagination_enabled,
        "page_size_default": api.page_size_default,
        "page_size_max": api.page_size_max,
        "timeout_seconds": api.timeout_seconds,
        "cache_ttl_seconds": api.cache_ttl_seconds,
        "max_rows": api.max_rows,
        "published_at": api.published_at.isoformat() if api.published_at else None,
        "created_at": api.created_at.isoformat() if api.created_at else None,
        "updated_at": api.updated_at.isoformat() if api.updated_at else None,
        "params": [
            {
                "id": p.id,
                "name": p.name,
                "param_in": p.param_in,
                "data_type": p.data_type,
                "required": p.required,
                "default_value": p.default_value,
                "description": p.description,
                "validator_regex": p.validator_regex,
                "sort_order": p.sort_order,
            }
            for p in sorted(api.params or [], key=lambda x: x.sort_order)
        ],
        "open_path": f"/open/v1/ws/{api.workspace_id}/{api.api_code}",
    }


def _normalize_param(p: Any) -> ApiParamIn:
    if isinstance(p, ApiParamIn):
        return p
    if isinstance(p, dict):
        return ApiParamIn(**p)
    raise HTTPException(status_code=400, detail="无效的 API 参数定义")


def _sync_params(db: Session, api: DataApi, params: List[Any]) -> None:
    db.query(DataApiParam).filter(DataApiParam.api_id == api.id).delete(synchronize_session=False)
    for i, raw in enumerate(params):
        p = _normalize_param(raw)
        if not (p.name or "").strip():
            continue
        db.add(
            DataApiParam(
                api_id=api.id,
                name=p.name.strip(),
                param_in=p.param_in or "query",
                data_type=p.data_type or "string",
                required=bool(p.required),
                default_value=p.default_value,
                description=p.description,
                validator_regex=p.validator_regex,
                sort_order=p.sort_order if p.sort_order else i,
            )
        )
    db.flush()


def _resolve_ds(db: Session, api: DataApi) -> DataSource:
    if not api.datasource_id:
        raise HTTPException(status_code=400, detail="API 未绑定数据源")
    ds = db.query(DataSource).filter(DataSource.id == api.datasource_id, DataSource.workspace_id == api.workspace_id).first()
    if not ds:
        raise HTTPException(status_code=400, detail="数据源不存在或不属于当前工作空间")
    return ds


@router.get("/apis")
def list_apis(
    workspace_id: int,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_SERVICE_READ)
    q = db.query(DataApi).filter(DataApi.workspace_id == workspace_id).order_by(DataApi.updated_at.desc())
    if status:
        q = q.filter(DataApi.status == status)
    rows = q.all()
    ds_map = {
        d.id: d.name
        for d in db.query(DataSource).filter(DataSource.workspace_id == workspace_id).all()
    }
    return [_api_out(r, ds_map.get(r.datasource_id)) for r in rows]


@router.post("/apis")
def create_api(
    body: DataApiCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    code = body.api_code.strip().lower()
    if not _API_CODE_RE.match(code):
        raise HTTPException(status_code=400, detail="api_code 须为小写字母开头，仅含 a-z0-9_，2–63 字符")
    if db.query(DataApi).filter(DataApi.workspace_id == body.workspace_id, DataApi.api_code == code).first():
        raise HTTPException(status_code=400, detail="api_code 已存在")
    if body.datasource_id:
        require_datasource_row(db, current_user, body.datasource_id)
    api = DataApi(
        workspace_id=body.workspace_id,
        api_code=code,
        name=body.name.strip(),
        description=body.description,
        mode=body.mode,
        http_method=(body.http_method or "GET").upper(),
        datasource_id=body.datasource_id,
        sql_template=body.sql_template,
        wizard_config=body.wizard_config,
        response_fields=body.response_fields,
        pagination_enabled=body.pagination_enabled,
        page_size_default=body.page_size_default,
        page_size_max=body.page_size_max,
        timeout_seconds=body.timeout_seconds,
        cache_ttl_seconds=body.cache_ttl_seconds,
        max_rows=body.max_rows,
        owner_id=current_user.id,
        created_by=current_user.id,
    )
    db.add(api)
    db.flush()
    _sync_params(db, api, body.params)
    if api.mode == "wizard" and body.wizard_config:
        db.refresh(api)
        api.sql_template = wizard_to_sql(body.wizard_config, list(api.params or []))
    db.commit()
    db.refresh(api)
    return _api_out(api)


@router.get("/apis/{api_id}")
def get_api(api_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    api = _require_api(db, api_id, current_user)
    ds = db.query(DataSource).filter(DataSource.id == api.datasource_id).first() if api.datasource_id else None
    return _api_out(api, ds.name if ds else None)


@router.put("/apis/{api_id}")
def update_api(
    api_id: int,
    body: DataApiUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api = _require_api(db, api_id, current_user)
    assert_workspace_data_capability(db, current_user, api.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    data = body.model_dump(exclude_unset=True, exclude={"params"})
    params = body.params if body.params is not None else None
    if "datasource_id" in data and data["datasource_id"]:
        require_datasource_row(db, current_user, data["datasource_id"])
    allowed = {
        "name", "description", "http_method", "datasource_id", "sql_template", "wizard_config",
        "response_fields", "pagination_enabled", "page_size_default", "page_size_max",
        "timeout_seconds", "cache_ttl_seconds", "max_rows", "mode",
    }
    for k, v in data.items():
        if k in allowed:
            setattr(api, k, v)
    if params is not None:
        _sync_params(db, api, params)
    if api.mode == "wizard" and api.wizard_config:
        db.refresh(api)
        api.sql_template = wizard_to_sql(api.wizard_config, list(api.params or []))
    api.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(api)
    ds = db.query(DataSource).filter(DataSource.id == api.datasource_id).first() if api.datasource_id else None
    return _api_out(api, ds.name if ds else None)


@router.delete("/apis/{api_id}")
def delete_api(api_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    api = _require_api(db, api_id, current_user)
    assert_workspace_data_capability(db, current_user, api.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    db.delete(api)
    db.commit()
    return {"ok": True}


@router.post("/apis/{api_id}/publish")
def publish_api(api_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    api = _require_api(db, api_id, current_user)
    assert_workspace_data_capability(db, current_user, api.workspace_id, "developer", PC.GIDO_SERVICE_RUN)
    assert_can_publish_production(db, current_user, api.workspace_id)
    return execute_data_api_publish(db, api, current_user)


@router.post("/apis/{api_id}/offline")
def offline_api(api_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    api = _require_api(db, api_id, current_user)
    assert_workspace_data_capability(db, current_user, api.workspace_id, "developer", PC.GIDO_SERVICE_RUN)
    assert_can_publish_production(db, current_user, api.workspace_id)
    return execute_data_api_offline(db, api)


@router.post("/apis/{api_id}/test")
def test_api(
    api_id: int,
    body: ApiTestIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    api = _require_api(db, api_id, current_user)
    assert_workspace_data_capability(db, current_user, api.workspace_id, "developer", PC.GIDO_SERVICE_RUN)
    ds = _resolve_ds(db, api)
    t0 = time.time()
    trace = new_trace_id()
    try:
        result = execute_data_api(
            db,
            api,
            ds,
            body.params,
            page_no=body.page_no,
            page_size=body.page_size,
            skip_cache=True,
        )
        latency = (time.time() - t0) * 1000
        db.add(
            DataApiInvocationLog(
                workspace_id=api.workspace_id,
                api_id=api.id,
                trace_id=trace,
                http_method="TEST",
                request_params=body.params,
                status_code=200,
                row_count=len(result.get("rows") or []),
                latency_ms=latency,
                cache_hit=False,
            )
        )
        db.commit()
        return {"trace_id": trace, "latency_ms": round(latency, 2), "data": result}
    except HTTPException as e:
        latency = (time.time() - t0) * 1000
        logger.warning("data-service test failed api_id=%s status=%s detail=%s", api_id, e.status_code, e.detail)
        db.add(
            DataApiInvocationLog(
                workspace_id=api.workspace_id,
                api_id=api.id,
                trace_id=trace,
                http_method="TEST",
                request_params=body.params,
                status_code=e.status_code,
                latency_ms=latency,
                error_message=str(e.detail),
            )
        )
        db.commit()
        raise
    except Exception as e:
        latency = (time.time() - t0) * 1000
        detail = str(e)
        db.add(
            DataApiInvocationLog(
                workspace_id=api.workspace_id,
                api_id=api.id,
                trace_id=trace,
                http_method="TEST",
                request_params=body.params,
                status_code=500,
                latency_ms=latency,
                error_message=detail[:2000],
            )
        )
        db.commit()
        raise HTTPException(status_code=400, detail=f"测试失败: {detail}") from e


@router.get("/apis/{api_id}/openapi")
def export_openapi(api_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    api = _require_api(db, api_id, current_user)
    params_schema = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    for p in api.params or []:
        params_schema["properties"][p.name] = {"type": p.data_type or "string", "description": p.description or ""}
        if p.required:
            params_schema["required"].append(p.name)
    path = f"/open/v1/ws/{api.workspace_id}/{api.api_code}"
    return {
        "openapi": "3.0.3",
        "info": {"title": api.name, "description": api.description or "", "version": str(api.version or 1)},
        "paths": {
            path: {
                (api.http_method or "get").lower(): {
                    "summary": api.name,
                    "parameters": [
                        {"name": p.name, "in": "query", "required": p.required, "schema": {"type": p.data_type or "string"}}
                        for p in (api.params or [])
                        if (p.param_in or "query") == "query"
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


# ---------- 消费者应用 ----------

@router.get("/apps")
def list_apps(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_SERVICE_READ)
    apps = db.query(ConsumerApp).filter(ConsumerApp.workspace_id == workspace_id).order_by(ConsumerApp.id.desc()).all()
    out = []
    for a in apps:
        grant_ids = [g.api_id for g in a.grants]
        out.append(
            {
                "id": a.id,
                "workspace_id": a.workspace_id,
                "name": a.name,
                "description": a.description,
                "app_key": a.app_key,
                "ip_whitelist": a.ip_whitelist,
                "qps_limit": a.qps_limit,
                "daily_quota": a.daily_quota,
                "is_active": a.is_active,
                "granted_api_ids": grant_ids,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
        )
    return out


@router.post("/apps")
def create_app(
    body: ConsumerAppCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    app_key, secret_plain, secret_hash = generate_app_credentials()
    app = ConsumerApp(
        workspace_id=body.workspace_id,
        name=body.name.strip(),
        description=body.description,
        app_key=app_key,
        app_secret_hash=secret_hash,
        ip_whitelist=body.ip_whitelist,
        qps_limit=body.qps_limit,
        daily_quota=body.daily_quota,
        created_by=current_user.id,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return {
        "id": app.id,
        "app_key": app_key,
        "app_secret": secret_plain,
        "message": "请妥善保存 app_secret，仅显示一次",
    }


@router.post("/apps/{app_id}/grants")
def grant_apis(
    app_id: int,
    body: GrantIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = db.query(ConsumerApp).filter(ConsumerApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    assert_workspace_data_capability(db, current_user, app.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    for api_id in body.api_ids:
        api = db.query(DataApi).filter(DataApi.id == api_id, DataApi.workspace_id == app.workspace_id).first()
        if not api:
            continue
        exists = db.query(ConsumerAppApiGrant).filter(ConsumerAppApiGrant.app_id == app.id, ConsumerAppApiGrant.api_id == api_id).first()
        if exists:
            exists.qps_limit = body.qps_limit
        else:
            db.add(ConsumerAppApiGrant(app_id=app.id, api_id=api_id, qps_limit=body.qps_limit))
    db.commit()
    return {"ok": True}


@router.delete("/apps/{app_id}/grants/{api_id}")
def revoke_grant(app_id: int, api_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    app = db.query(ConsumerApp).filter(ConsumerApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    assert_workspace_data_capability(db, current_user, app.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    db.query(ConsumerAppApiGrant).filter(ConsumerAppApiGrant.app_id == app_id, ConsumerAppApiGrant.api_id == api_id).delete()
    db.commit()
    return {"ok": True}


@router.delete("/apps/{app_id}")
def delete_app(app_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    app = db.query(ConsumerApp).filter(ConsumerApp.id == app_id).first()
    if not app:
        raise HTTPException(status_code=404, detail="应用不存在")
    assert_workspace_data_capability(db, current_user, app.workspace_id, "developer", PC.GIDO_SERVICE_WRITE)
    db.delete(app)
    db.commit()
    return {"ok": True}


# ---------- 监控 ----------

@router.get("/stats")
def invocation_stats(
    workspace_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_SERVICE_READ)
    since = datetime.utcnow() - timedelta(days=days)
    total = (
        db.query(func.count(DataApiInvocationLog.id))
        .filter(DataApiInvocationLog.workspace_id == workspace_id, DataApiInvocationLog.created_at >= since)
        .scalar()
        or 0
    )
    errors = (
        db.query(func.count(DataApiInvocationLog.id))
        .filter(
            DataApiInvocationLog.workspace_id == workspace_id,
            DataApiInvocationLog.created_at >= since,
            DataApiInvocationLog.status_code >= 400,
        )
        .scalar()
        or 0
    )
    avg_lat = (
        db.query(func.avg(DataApiInvocationLog.latency_ms))
        .filter(DataApiInvocationLog.workspace_id == workspace_id, DataApiInvocationLog.created_at >= since)
        .scalar()
    )
    by_api = (
        db.query(DataApiInvocationLog.api_id, func.count(DataApiInvocationLog.id))
        .filter(DataApiInvocationLog.workspace_id == workspace_id, DataApiInvocationLog.created_at >= since)
        .group_by(DataApiInvocationLog.api_id)
        .all()
    )
    api_names = {a.id: a.name for a in db.query(DataApi).filter(DataApi.workspace_id == workspace_id).all()}
    return {
        "days": days,
        "total_calls": total,
        "error_calls": errors,
        "error_rate": round(errors / total, 4) if total else 0,
        "avg_latency_ms": round(float(avg_lat), 2) if avg_lat else 0,
        "top_apis": [{"api_id": aid, "api_name": api_names.get(aid), "calls": cnt} for aid, cnt in by_api[:20]],
    }


@router.get("/logs")
def list_logs(
    workspace_id: int,
    api_id: Optional[int] = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_SERVICE_READ)
    q = db.query(DataApiInvocationLog).filter(DataApiInvocationLog.workspace_id == workspace_id)
    if api_id:
        q = q.filter(DataApiInvocationLog.api_id == api_id)
    rows = q.order_by(DataApiInvocationLog.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "api_id": r.api_id,
            "app_id": r.app_id,
            "trace_id": r.trace_id,
            "status_code": r.status_code,
            "row_count": r.row_count,
            "latency_ms": r.latency_ms,
            "cache_hit": r.cache_hit,
            "error_message": r.error_message,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


class WizardPreviewIn(BaseModel):
    wizard_config: dict
    params: List[ApiParamIn] = []


@router.post("/wizard/preview-sql")
def preview_wizard_sql(body: WizardPreviewIn, current_user: User = Depends(get_current_user)):
    params = [DataApiParam(name=p.name, data_type=p.data_type, required=p.required) for p in body.params]
    return {"sql_template": wizard_to_sql(body.wizard_config, params)}

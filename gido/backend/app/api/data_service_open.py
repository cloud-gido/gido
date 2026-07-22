# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据服务开放网关：AppKey + AppSecret 鉴权，对外提供 HTTP API。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models.data_service import ConsumerApp, DataApi, DataApiInvocationLog
from app.services.data_api_engine import (
    check_ip_whitelist,
    check_rate_limit,
    execute_data_api,
    verify_app_secret,
)
from app.services.data_api_response import (
    new_trace_id,
    open_error_envelope,
    open_success_envelope,
    pop_pagination_params,
)

open_router = APIRouter(prefix="/open/v1", tags=["数据服务-开放网关"])


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _auth_app(
    db: Session,
    workspace_id: int,
    app_key: str,
    app_secret: str,
    client_ip: str,
) -> ConsumerApp:
    app = (
        db.query(ConsumerApp)
        .options(joinedload(ConsumerApp.grants))
        .filter(ConsumerApp.workspace_id == workspace_id, ConsumerApp.app_key == app_key)
        .first()
    )
    if not app or not app.is_active:
        raise HTTPException(status_code=401, detail="无效的应用凭证")
    if not verify_app_secret(app, app_secret):
        raise HTTPException(status_code=401, detail="无效的应用凭证")
    if not check_ip_whitelist(client_ip, app.ip_whitelist):
        raise HTTPException(status_code=403, detail="IP 不在白名单")
    return app


def _error_response(exc: HTTPException, *, trace_id: Optional[str] = None) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=open_error_envelope(
            str(exc.detail),
            http_status=exc.status_code,
            trace_id=trace_id,
        ),
    )


def _invoke_api(
    db: Session,
    api: DataApi,
    app: Optional[ConsumerApp],
    raw_params: Dict[str, Any],
    request: Request,
    *,
    page: int = 1,
    page_size: Optional[int] = None,
) -> Union[dict, JSONResponse]:
    from app.models.workspace import DataSource

    if api.status != "online":
        raise HTTPException(status_code=404, detail="API 未上线或已下线")

    if app:
        grant = next((g for g in app.grants if g.api_id == api.id), None)
        if not grant:
            raise HTTPException(status_code=403, detail="应用未授权访问此 API")
        limit = grant.qps_limit or app.qps_limit or 100
        check_rate_limit(f"app:{app.app_key}:api:{api.id}", limit)

    ds = db.query(DataSource).filter(DataSource.id == api.datasource_id).first()
    if not ds:
        raise HTTPException(status_code=500, detail="API 数据源配置无效")

    trace = new_trace_id()
    t0 = time.time()
    status = 200
    err_msg = None
    result: dict = {}
    row_count = 0
    http_exc: Optional[HTTPException] = None
    try:
        result = execute_data_api(db, api, ds, raw_params, page_no=page, page_size=page_size)
        row_count = len(result.get("list") or [])
    except HTTPException as e:
        status = e.status_code
        err_msg = str(e.detail)
        http_exc = e
    finally:
        latency = (time.time() - t0) * 1000
        db.add(
            DataApiInvocationLog(
                workspace_id=api.workspace_id,
                api_id=api.id,
                app_id=app.id if app else None,
                trace_id=trace,
                http_method=request.method,
                client_ip=_client_ip(request),
                request_params=raw_params,
                status_code=status,
                row_count=row_count,
                latency_ms=latency,
                cache_hit=bool(result.get("cache_hit")) if status == 200 else False,
                error_message=err_msg,
            )
        )
        db.commit()

    if http_exc is not None:
        return _error_response(http_exc, trace_id=trace)
    return open_success_envelope(trace, result)


@open_router.get("/ws/{workspace_id}/{api_code}")
@open_router.post("/ws/{workspace_id}/{api_code}")
async def invoke_data_api(
    workspace_id: int,
    api_code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        app_key = request.headers.get("x-app-key") or request.headers.get("X-App-Key")
        app_secret = request.headers.get("x-app-secret") or request.headers.get("X-App-Secret")
        if not app_key or not app_secret:
            raise HTTPException(status_code=401, detail="缺少 X-App-Key / X-App-Secret 请求头")

        app = _auth_app(db, workspace_id, app_key.strip(), app_secret.strip(), _client_ip(request))

        api = (
            db.query(DataApi)
            .options(joinedload(DataApi.params))
            .filter(DataApi.workspace_id == workspace_id, DataApi.api_code == api_code.lower())
            .first()
        )
        if not api:
            raise HTTPException(status_code=404, detail="API 不存在")

        raw_params: Dict[str, Any] = {}
        if request.method == "GET":
            raw_params = dict(request.query_params)
        else:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    raw_params = dict(body)
            except Exception:
                raw_params = dict(request.query_params)

        page, page_size = pop_pagination_params(raw_params)
        return _invoke_api(db, api, app, raw_params, request, page=page, page_size=page_size)
    except HTTPException as e:
        return _error_response(e)

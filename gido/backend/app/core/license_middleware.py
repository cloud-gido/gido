# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""许可无效时拒绝写操作（白名单除外）。"""
from __future__ import annotations

import json
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.license_gate import LICENSE_UPGRADE_REQUIRED, license_http_detail
from app.services import license_state

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}

# 前缀或精确路径白名单（许可无效时仍允许）
_ALLOW_PREFIXES = (
    "/health",
    "/ready",
    "/docs",
    "/openapi",
    "/redoc",
    "/api/license",
    "/api/auth/login",
    "/api/open/",  # 对外开放数据 API
)

_ALLOW_EXACT = {
    "/",
    "/api/auth/me",
}


class LicenseWriteGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method not in _WRITE:
            return await call_next(request)
        path = request.url.path or ""
        if path in _ALLOW_EXACT or any(path.startswith(p) for p in _ALLOW_PREFIXES):
            return await call_next(request)
        snap = license_state.get_snapshot()
        if snap.mode == "open" or snap.plan != "invalid":
            return await call_next(request)
        body = license_http_detail(
            "当前许可无效或已吊销，系统处于只读模式，请联系管理员续期或升级。",
            LICENSE_UPGRADE_REQUIRED,
        )
        return JSONResponse(status_code=403, content={"detail": body})

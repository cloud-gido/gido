# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""许可状态 API（前端顶栏 / 横幅）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.workspace import User
from app.services import license_state

router = APIRouter(prefix="/license", tags=["许可"])


@router.get("/status")
def license_status(_: User = Depends(get_current_user)):
    snap = license_state.get_snapshot()
    return {"ok": True, **snap.to_public_dict()}


@router.post("/refresh")
def license_refresh(_: User = Depends(get_current_user)):
    snap = license_state.refresh(force_activate=False)
    return {"ok": True, **snap.to_public_dict()}

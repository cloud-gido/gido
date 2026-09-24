# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""License feature / quota 门禁。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from app.services import license_state


LICENSE_UPGRADE_REQUIRED = "LICENSE_UPGRADE_REQUIRED"


def _snapshot():
    return license_state.get_snapshot()


def license_http_detail(message: str, code: str = LICENSE_UPGRADE_REQUIRED) -> dict[str, Any]:
    snap = _snapshot()
    return {
        "code": code,
        "message": message,
        "plan": snap.plan,
        "reason": snap.reason,
    }


def raise_license(message: str, status_code: int = 403) -> None:
    raise HTTPException(status_code=status_code, detail=license_http_detail(message))


def assert_writes_allowed() -> None:
    snap = _snapshot()
    if snap.mode == "open":
        return
    if snap.plan == "invalid":
        raise_license("当前许可无效或已吊销，系统处于只读模式，请联系管理员续期或升级。")


def assert_feature(name: str, message: Optional[str] = None) -> None:
    snap = _snapshot()
    if snap.mode == "open":
        return
    if snap.plan == "invalid":
        raise_license("当前许可无效，无法使用该功能，请联系管理员续期。")
    feats = snap.features or {}
    if not feats.get(name):
        raise_license(message or f"当前套餐（{snap.plan}）不包含能力「{name}」，请升级企业版。")


def assert_max_users(current_count: int) -> None:
    snap = _snapshot()
    if snap.mode == "open":
        return
    if snap.plan == "invalid":
        raise_license("当前许可无效，无法创建用户。")
    max_users = int((snap.features or {}).get("max_users") or 0)
    if current_count >= max_users:
        raise_license(
            f"当前套餐最多 {max_users} 个用户（已有 {current_count}），请升级套餐后再创建。"
        )


def assert_can_create_workspace(current_count: int) -> None:
    snap = _snapshot()
    if snap.mode == "open":
        return
    if snap.plan == "invalid":
        raise_license("当前许可无效，无法创建工作空间。")
    feats = snap.features or {}
    if not feats.get("multi_workspace") and current_count >= 1:
        raise_license("当前为标准版/单空间许可，无法创建更多工作空间，请升级企业版。")
    max_ws = int(feats.get("max_workspaces") or 1)
    if current_count >= max_ws:
        raise_license(
            f"当前套餐最多 {max_ws} 个工作空间（已有 {current_count}），请升级套餐。"
        )


def feature_enabled(name: str) -> bool:
    snap = _snapshot()
    if snap.mode == "open":
        return True
    if snap.plan == "invalid":
        return False
    return bool((snap.features or {}).get(name))

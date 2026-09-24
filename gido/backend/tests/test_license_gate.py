# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""License gate / state unit tests (no network)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.license_state import (
    INVALID_FEATURES,
    LicenseSnapshot,
    STANDARD_FEATURES,
    _set_state,
    get_snapshot,
    is_open_mode,
    refresh,
)
from app.core import license_gate


@pytest.fixture(autouse=True)
def _reset_open(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LICENSE_MODE", "open")
    monkeypatch.setattr("app.core.config.settings.LICENSE_KEY", None)
    monkeypatch.setattr("app.core.config.settings.DEPLOYMENT_ID", None)
    refresh()
    yield


def test_open_mode_allows_all():
    assert is_open_mode()
    snap = get_snapshot()
    assert snap.plan == "enterprise"
    assert snap.mode == "open"
    license_gate.assert_can_create_workspace(10)
    license_gate.assert_max_users(100)
    license_gate.assert_feature("rbac_advanced")


def test_standard_blocks_second_workspace(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LICENSE_MODE", "commercial")
    monkeypatch.setattr("app.core.config.settings.LICENSE_KEY", "gido_x")
    monkeypatch.setattr("app.core.config.settings.DEPLOYMENT_ID", "00000000-0000-0000-0000-000000000001")
    _set_state(
        LicenseSnapshot(
            plan="standard",
            features=dict(STANDARD_FEATURES),
            mode="commercial",
            reason="expired",
            source="test",
        )
    )
    license_gate.assert_can_create_workspace(0)
    with pytest.raises(HTTPException) as ei:
        license_gate.assert_can_create_workspace(1)
    assert ei.value.status_code == 403
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == license_gate.LICENSE_UPGRADE_REQUIRED


def test_invalid_blocks_writes(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LICENSE_MODE", "commercial")
    monkeypatch.setattr("app.core.config.settings.LICENSE_KEY", "gido_x")
    monkeypatch.setattr("app.core.config.settings.DEPLOYMENT_ID", "00000000-0000-0000-0000-000000000001")
    _set_state(
        LicenseSnapshot(
            plan="invalid",
            features=dict(INVALID_FEATURES),
            mode="commercial",
            reason="revoked",
            source="test",
        )
    )
    with pytest.raises(HTTPException):
        license_gate.assert_writes_allowed()
    with pytest.raises(HTTPException):
        license_gate.assert_feature("sso")


def test_max_users_standard(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.LICENSE_MODE", "commercial")
    monkeypatch.setattr("app.core.config.settings.LICENSE_KEY", "gido_x")
    monkeypatch.setattr("app.core.config.settings.DEPLOYMENT_ID", "00000000-0000-0000-0000-000000000001")
    _set_state(
        LicenseSnapshot(
            plan="standard",
            features=dict(STANDARD_FEATURES),
            mode="commercial",
            reason="expired",
            source="test",
        )
    )
    license_gate.assert_max_users(4)
    with pytest.raises(HTTPException):
        license_gate.assert_max_users(5)

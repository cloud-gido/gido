# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""平台管理能力以角色为准，is_admin 随角色同步。"""
from types import SimpleNamespace

from app.core.access import (
    is_platform_admin,
    role_grants_platform_admin,
    sync_is_admin_with_role,
)


def test_role_grants_platform_admin():
    assert role_grants_platform_admin("super_admin")
    assert role_grants_platform_admin("platform_admin")
    assert not role_grants_platform_admin("developer")
    assert not role_grants_platform_admin(None)


def test_sync_is_admin_with_role():
    u = SimpleNamespace(username="alice", is_admin=False, system_role=None)
    sync_is_admin_with_role(u, SimpleNamespace(code="platform_admin"))
    assert u.is_admin is True
    sync_is_admin_with_role(u, SimpleNamespace(code="developer"))
    assert u.is_admin is False
    admin = SimpleNamespace(username="admin", is_admin=False, system_role=None)
    sync_is_admin_with_role(admin, SimpleNamespace(code="developer"))
    assert admin.is_admin is True


def test_is_platform_admin_prefers_role():
    u = SimpleNamespace(
        username="bob",
        is_admin=False,
        system_role=SimpleNamespace(code="super_admin"),
    )
    assert is_platform_admin(u) is True
    u2 = SimpleNamespace(username="carol", is_admin=False, system_role=SimpleNamespace(code="analyst"))
    assert is_platform_admin(u2) is False

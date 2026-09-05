# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""默认空间 infras：平台角色 → 空间成员档位映射。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import get_password_hash
from app.models.rbac_models import Role
from app.models.workspace import User, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap
from app.models import rbac_models  # noqa: F401
from app.services.workspace_default import (
    ensure_default_workspace_membership,
    space_role_for_platform_user,
)


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    run_rbac_bootstrap(db)
    roles = {r.code: r for r in db.query(Role).all()}
    admin = User(
        username="admin",
        email="a@t.com",
        hashed_password=get_password_hash("x"),
        is_admin=True,
        is_active=True,
        role_id=roles["platform_admin"].id,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    ws = Workspace(name="infras", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    db.commit()
    return db, roles


def test_space_role_mapping_by_platform_code():
    db, roles = _db()
    for code, expect in (
        ("analyst", "viewer"),
        ("developer", "developer"),
        ("operator", "developer"),
        ("workspace_steward", "developer"),
        ("platform_admin", "admin"),
        ("super_admin", "admin"),
    ):
        u = User(
            username=f"u_{code}",
            email=f"{code}@t.com",
            hashed_password="x",
            is_admin=False,
            is_active=True,
            role_id=roles[code].id,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        assert space_role_for_platform_user(db, u) == expect, code
    db.close()


def test_ensure_joins_analyst_as_viewer():
    db, roles = _db()
    u = User(
        username="analyst1",
        email="an1@t.com",
        hashed_password="x",
        is_admin=False,
        is_active=True,
        role_id=roles["analyst"].id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    res = ensure_default_workspace_membership(db, u)
    assert res["created"] is True
    assert res["member_role"] == "viewer"
    assert res["workspace_name"] == "infras"
    row = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.user_id == u.id, WorkspaceMember.workspace_id == res["workspace_id"])
        .first()
    )
    assert row is not None and row.role == "viewer"
    # 幂等
    res2 = ensure_default_workspace_membership(db, u)
    assert res2["created"] is False
    assert res2["member_role"] == "viewer"
    db.close()

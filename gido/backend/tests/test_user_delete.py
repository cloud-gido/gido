# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""删除平台用户：解除成员引用后可删。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-user-delete")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import get_password_hash
from app.models.workspace import User, Workspace, WorkspaceMember
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.services.user_delete import delete_platform_user


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    admin = User(
        id=1,
        username="admin",
        email="a@t.local",
        hashed_password=get_password_hash("x"),
        is_admin=True,
        is_active=True,
    )
    victim = User(
        id=35,
        username="test",
        email="t@t.local",
        hashed_password=get_password_hash("x"),
        is_admin=False,
        is_active=True,
    )
    s.add_all([admin, victim])
    s.commit()
    ws = Workspace(id=1, name="infras", owner_id=1)
    s.add(ws)
    s.commit()
    s.add(WorkspaceMember(workspace_id=1, user_id=35, role="viewer"))
    s.commit()
    yield s
    s.close()


def test_delete_user_with_workspace_membership(db):
    admin = db.query(User).filter(User.id == 1).one()
    victim = db.query(User).filter(User.id == 35).one()
    delete_platform_user(db, victim, actor=admin)
    assert db.query(User).filter(User.id == 35).first() is None
    assert db.query(WorkspaceMember).filter(WorkspaceMember.user_id == 35).count() == 0


def test_delete_workspace_owner_blocked(db):
    admin = db.query(User).filter(User.id == 1).one()
    # make victim an owner of another ws
    victim = db.query(User).filter(User.id == 35).one()
    db.add(Workspace(name="owned_by_test", owner_id=35))
    db.commit()
    with pytest.raises(HTTPException) as ei:
        delete_platform_user(db, victim, actor=admin)
    assert ei.value.status_code == 400
    assert "负责人" in str(ei.value.detail)
    assert db.query(User).filter(User.id == 35).first() is not None

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""权限能力集成：运维可运行不可写；开发可写。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-rbac-capability")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.rbac_models import Role
from app.models.workspace import User, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.api import auth, studio, data_service
import app.api.streaming  # noqa: F401


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    run_rbac_bootstrap(db)
    roles = {r.code: r for r in db.query(Role).all()}

    admin = User(
        username="admin",
        email="admin@gido.com",
        hashed_password=get_password_hash("admin123"),
        is_admin=True,
        is_active=True,
        role_id=roles["platform_admin"].id,
    )
    ops = User(
        username="ops",
        email="ops@gido.com",
        hashed_password=get_password_hash("ops123"),
        is_admin=False,
        is_active=True,
        role_id=roles["operator"].id,
    )
    dev = User(
        username="dev",
        email="dev@gido.com",
        hashed_password=get_password_hash("dev123"),
        is_admin=False,
        is_active=True,
        role_id=roles["developer"].id,
    )
    db.add_all([admin, ops, dev])
    db.commit()
    for u in (admin, ops, dev):
        db.refresh(u)

    ws = Workspace(name="infras", description="default", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    # 运维/开发均为空间 developer（能进 Studio），平台角色决定 write/run
    for u in (admin, ops, dev):
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="developer"))
    db.commit()
    run_rbac_bootstrap(db)
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(studio.router, prefix="/api")
    app.include_router(data_service.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        c.ws_id = ws.id  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _login(c: TestClient, username: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_operator_cannot_create_studio_node_but_developer_can(client: TestClient):
    ops_tok = _login(client, "ops", "ops123")
    r = client.post(
        "/api/studio/nodes",
        headers=_h(ops_tok),
        json={
            "workspace_id": client.ws_id,
            "name": "ops_script",
            "node_type": "SQL",
            "script_content": "SELECT 1",
        },
    )
    assert r.status_code == 403, r.text

    dev_tok = _login(client, "dev", "dev123")
    r2 = client.post(
        "/api/studio/nodes",
        headers=_h(dev_tok),
        json={
            "workspace_id": client.ws_id,
            "name": "dev_script",
            "node_type": "SQL",
            "script_content": "SELECT 1",
        },
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == "dev_script"


def test_operator_can_list_studio_nodes(client: TestClient):
    """运维有 studio:read，且空间角色 developer，应能列表。"""
    # 先用开发造一条
    dev_tok = _login(client, "dev", "dev123")
    client.post(
        "/api/studio/nodes",
        headers=_h(dev_tok),
        json={
            "workspace_id": client.ws_id,
            "name": "shared",
            "node_type": "SQL",
            "script_content": "SELECT 1",
        },
    )
    ops_tok = _login(client, "ops", "ops123")
    r = client.get(
        f"/api/studio/nodes?workspace_id={client.ws_id}",
        headers=_h(ops_tok),
    )
    assert r.status_code == 200, r.text
    assert any(n.get("name") == "shared" for n in r.json())


def test_operator_cannot_create_serve_api(client: TestClient):
    ops_tok = _login(client, "ops", "ops123")
    r = client.post(
        "/api/data-service/apis",
        headers=_h(ops_tok),
        json={
            "workspace_id": client.ws_id,
            "api_code": "ops_api",
            "name": "Ops API",
            "mode": "sql",
            "sql_template": "SELECT 1 AS x",
        },
    )
    assert r.status_code == 403, r.text

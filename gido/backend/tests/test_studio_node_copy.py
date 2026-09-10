# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Studio 节点复制：同目录、内容拷贝、名称去重、无发布锁。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-studio-copy")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.workspace import User, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap
import app.api.streaming  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.api import auth, workspace, studio


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
    admin = User(
        username="admin",
        email="admin@gido.com",
        full_name="管理员",
        hashed_password=get_password_hash("admin123"),
        is_admin=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    ws0 = Workspace(name="infras", description="default", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws0)
    db.commit()
    db.refresh(ws0)
    db.add(WorkspaceMember(workspace_id=ws0.id, user_id=admin.id, role="admin"))
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
    app.include_router(workspace.router, prefix="/api")
    app.include_router(studio.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(c: TestClient) -> tuple[str, int]:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    wss = c.get("/api/workspaces", headers={"Authorization": f"Bearer {token}"})
    assert wss.status_code == 200, wss.text
    return token, wss.json()[0]["id"]


def test_copy_node_same_folder_and_unique_name(client: TestClient):
    token, ws_id = _login(client)
    h = {"Authorization": f"Bearer {token}"}

    folder = client.post(
        "/api/studio/folders",
        headers=h,
        json={"workspace_id": ws_id, "name": "copy_dir", "parent_id": None},
    )
    assert folder.status_code == 200, folder.text
    folder_id = folder.json()["id"]

    src = client.post(
        "/api/studio/nodes",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "ads_demo",
            "node_type": "SQL",
            "script_content": "SELECT 1 AS x",
            "folder_id": folder_id,
            "params": {"env": "dev"},
        },
    )
    assert src.status_code == 200, src.text
    src_body = src.json()

    copied = client.post(f"/api/studio/nodes/{src_body['id']}/copy", headers=h, json={})
    assert copied.status_code == 200, copied.text
    body = copied.json()
    assert body["id"] != src_body["id"]
    assert body["name"] == "ads_demo-copy"
    assert body["folder_id"] == folder_id
    assert body["script_content"] == "SELECT 1 AS x"
    assert body["node_type"] == "SQL"
    assert body["params"] == {"env": "dev"}
    assert body["is_published"] is False
    assert body["is_locked"] is False

    again = client.post(f"/api/studio/nodes/{src_body['id']}/copy", headers=h, json={})
    assert again.status_code == 200, again.text
    assert again.json()["name"] == "ads_demo-copy-2"

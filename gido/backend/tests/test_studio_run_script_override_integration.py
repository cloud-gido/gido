# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
Studio 试跑 script_content 覆盖：请求级临时脚本，不得写回 TaskNode。
对齐 Cmd+Enter 选中片段试跑，避免污染草稿。
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-studio-run-override")

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
import app.api.studio  # noqa: F401
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


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_run_node_script_override_does_not_persist(client: TestClient):
    """Cmd+Enter 选中片段：body.script_content 仅当次执行，库内正文不变。"""
    token, ws_id = _login(client)
    h = _h(token)
    saved = "echo SAVED_FULL_SCRIPT"
    override = "echo OVERRIDE_FRAGMENT"
    created = client.post(
        "/api/studio/nodes",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "run-override-shell",
            "node_type": "SHELL",
            "script_content": saved,
        },
    )
    assert created.status_code == 200, created.text
    nid = created.json()["id"]
    assert created.json()["script_content"] == saved

    ran = client.post(
        f"/api/studio/nodes/{nid}/run",
        headers=h,
        json={"script_content": override},
    )
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["status"] == "success"
    assert "OVERRIDE_FRAGMENT" in (body.get("log") or "")
    assert "SAVED_FULL_SCRIPT" not in (body.get("log") or "")

    detail = client.get(f"/api/studio/nodes/{nid}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json()["script_content"] == saved


def test_run_node_without_override_keeps_saved_script(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    saved = "echo ONLY_SAVED"
    created = client.post(
        "/api/studio/nodes",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "run-no-override",
            "node_type": "SHELL",
            "script_content": saved,
        },
    )
    assert created.status_code == 200, created.text
    nid = created.json()["id"]

    ran = client.post(f"/api/studio/nodes/{nid}/run", headers=h, json={})
    assert ran.status_code == 200, ran.text
    assert "ONLY_SAVED" in (ran.json().get("log") or "")

    detail = client.get(f"/api/studio/nodes/{nid}", headers=h)
    assert detail.json()["script_content"] == saved

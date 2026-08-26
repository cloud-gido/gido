# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
实时作业列表瘦身 + 详情接口：集成 / 回归。

约定：
- GET /streaming/jobs 默认不含 script_content / generated_artifact（content_loaded=false）
- GET /streaming/jobs/{id} 返回完整正文（content_loaded=true）
- 创建 / 更新 / 挪目录等写接口仍返回完整详情
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-stream-list-detail")

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
from app.api import auth, workspace, streaming


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
    app.include_router(streaming.router, prefix="/api")
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


def test_list_jobs_omits_heavy_fields_get_job_loads_them(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    script = "SELECT /* heavy */ 1 AS id\n" + ("-- pad\n" * 50)
    created = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-list-slim",
            "job_type": "SQL",
            "script_content": script,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    jid = body["id"]
    assert body["content_loaded"] is True
    assert body["script_content"] == script

    listed = client.get("/api/streaming/jobs", headers=h, params={"workspace_id": ws_id})
    assert listed.status_code == 200
    row = next(x for x in listed.json() if x["id"] == jid)
    assert row["name"] == "job-list-slim"
    assert row["job_type"] == "SQL"
    assert row["content_loaded"] is False
    assert row["script_content"] is None
    assert row["generated_artifact"] is None

    detail = client.get(f"/api/streaming/jobs/{jid}", headers=h)
    assert detail.status_code == 200, detail.text
    full = detail.json()
    assert full["content_loaded"] is True
    assert full["script_content"] == script
    assert full["id"] == jid


def test_get_job_404_for_missing(client: TestClient):
    token, _ws_id = _login(client)
    r = client.get("/api/streaming/jobs/999999", headers=_h(token))
    assert r.status_code == 404


def test_update_and_draft_still_return_full_payload(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    jid = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-draft-full",
            "job_type": "SQL",
            "script_content": "SELECT 1",
        },
    ).json()["id"]

    draft = client.put(
        f"/api/streaming/jobs/{jid}",
        headers=h,
        params={"create_history": False},
        json={"script_content": "SELECT 2"},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["content_loaded"] is True
    assert draft.json()["script_content"] == "SELECT 2"

    listed = client.get("/api/streaming/jobs", headers=h, params={"workspace_id": ws_id}).json()
    row = next(x for x in listed if x["id"] == jid)
    assert row["content_loaded"] is False
    assert row["script_content"] is None
    assert client.get(f"/api/streaming/jobs/{jid}", headers=h).json()["script_content"] == "SELECT 2"


def test_jar_job_list_still_marks_content_not_loaded(client: TestClient):
    """JAR 本来就没有 script；列表仍应标记 content_loaded=false，打开走 getJob。"""
    token, ws_id = _login(client)
    h = _h(token)
    created = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-jar-slim",
            "job_type": "JAR",
            "main_class": "com.example.Main",
            "program_args": "--a 1",
        },
    )
    assert created.status_code == 200, created.text
    jid = created.json()["id"]
    assert created.json()["content_loaded"] is True

    row = next(
        x
        for x in client.get("/api/streaming/jobs", headers=h, params={"workspace_id": ws_id}).json()
        if x["id"] == jid
    )
    assert row["content_loaded"] is False
    assert row["script_content"] is None
    detail = client.get(f"/api/streaming/jobs/{jid}", headers=h).json()
    assert detail["content_loaded"] is True
    assert detail["main_class"] == "com.example.Main"
    assert detail["program_args"] == "--a 1"

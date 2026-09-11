# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""基线配置接口：校验、权限与幂等。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-sla-api")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import alert, auth
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models import rbac_models  # noqa: F401
from app.models.rbac_models import Role
from app.models.workspace import User, Workflow, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap


def _client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    run_rbac_bootstrap(db)
    roles = {r.code: r for r in db.query(Role).all()}
    admin = User(
        username="admin", email="admin@gido.com", hashed_password=get_password_hash("admin123"),
        is_admin=True, is_active=True, role_id=roles["platform_admin"].id,
    )
    viewer = User(
        username="viewer", email="v@gido.com", hashed_password=get_password_hash("viewer123"),
        is_admin=False, is_active=True, role_id=roles["analyst"].id,
    )
    db.add_all([admin, viewer])
    db.commit()
    for u in (admin, viewer):
        db.refresh(u)
    ws = Workspace(name="infras", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=viewer.id, role="viewer"))
    published = Workflow(name="ads_daily", workspace_id=ws.id, is_active=True, status="published")
    draft = Workflow(name="wip", workspace_id=ws.id, is_active=True, status="draft")
    db.add_all([published, draft])
    db.commit()
    db.refresh(published)
    ws_id, wf_id = int(ws.id), int(published.id)
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(alert.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), ws_id, wf_id


def _auth(c: TestClient, username: str, password: str) -> dict:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_list_only_covers_published_workflows():
    c, ws_id, wf_id = _client()
    h = _auth(c, "admin", "admin123")

    r = c.get("/api/alerts/sla/rules", headers=h, params={"workspace_id": ws_id})
    assert r.status_code == 200, r.text
    body = r.json()
    # 草稿工作流不在生产调度上，配基线没有意义
    assert [i["workflow_name"] for i in body["items"]] == ["ads_daily"]
    assert body["covered"] == 0
    assert body["items"][0]["rule"] is None


def test_upsert_then_update_is_idempotent():
    c, ws_id, wf_id = _client()
    h = _auth(c, "admin", "admin123")

    r = c.put(
        f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id},
        json={"expect_finish_time": "09:30", "expect_finish_offset_days": 1, "max_duration_minutes": 90},
    )
    assert r.status_code == 200, r.text
    assert r.json()["expect_finish_time"] == "09:30"
    assert r.json()["max_duration_minutes"] == 90

    r2 = c.put(
        f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id},
        json={"expect_finish_time": "10:00", "expect_finish_offset_days": 1},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r.json()["id"]
    assert r2.json()["expect_finish_time"] == "10:00"
    # 这次没传时长，应该被清掉而不是留着旧值
    assert r2.json()["max_duration_minutes"] is None

    body = c.get("/api/alerts/sla/rules", headers=h, params={"workspace_id": ws_id}).json()
    assert body["covered"] == 1


def test_rejects_bad_time_and_empty_rule():
    c, ws_id, wf_id = _client()
    h = _auth(c, "admin", "admin123")

    bad_time = c.put(
        f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id},
        json={"expect_finish_time": "9点半"},
    )
    assert bad_time.status_code == 400
    assert "HH:MM" in bad_time.json()["detail"]

    empty = c.put(
        f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id}, json={},
    )
    assert empty.status_code == 400

    bad_minutes = c.put(
        f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id},
        json={"max_duration_minutes": 0},
    )
    assert bad_minutes.status_code == 400


def test_viewer_cannot_write_but_can_read():
    c, ws_id, wf_id = _client()
    h = _auth(c, "viewer", "viewer123")

    assert c.get("/api/alerts/sla/rules", headers=h, params={"workspace_id": ws_id}).status_code == 200
    r = c.put(
        f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id},
        json={"expect_finish_time": "09:30"},
    )
    assert r.status_code == 403


def test_delete_is_safe_when_not_configured():
    c, ws_id, wf_id = _client()
    h = _auth(c, "admin", "admin123")

    r = c.delete(f"/api/alerts/sla/rules/{wf_id}", headers=h, params={"workspace_id": ws_id})
    assert r.status_code == 200
    assert r.json()["message"] == "未配置基线"


def test_unknown_workflow_is_404():
    c, ws_id, _ = _client()
    h = _auth(c, "admin", "admin123")

    r = c.put(
        "/api/alerts/sla/rules/999999", headers=h, params={"workspace_id": ws_id},
        json={"expect_finish_time": "09:30"},
    )
    assert r.status_code == 404

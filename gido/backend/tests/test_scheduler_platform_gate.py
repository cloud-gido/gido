# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Scheduler / 平台集成门禁：非平台管理员不可调用运维面接口。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-scheduler-rbac")

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
from app.api import auth, scheduler, admin_integration


def _client():
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
    analyst = User(
        username="analyst",
        email="a@gido.com",
        hashed_password=get_password_hash("analyst123"),
        is_admin=False,
        is_active=True,
        role_id=roles["analyst"].id,
    )
    db.add_all([admin, analyst])
    db.commit()
    for u in (admin, analyst):
        db.refresh(u)
    ws = Workspace(name="infras", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=analyst.id, role="viewer"))
    db.commit()
    ws_id = int(ws.id)
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(scheduler.router, prefix="/api")
    app.include_router(admin_integration.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), ws_id


def _login(c: TestClient, username: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_scheduler_ops_require_platform_manager():
    c, _ = _client()
    tok = _login(c, "analyst", "analyst123")
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/scheduler/jobs", headers=h).status_code == 403
    assert c.get("/api/scheduler/ds/status", headers=h).status_code == 403
    assert c.post("/api/scheduler/reload", headers=h).status_code == 403
    assert c.post("/api/scheduler/ds/sync-instances", headers=h).status_code == 403

    admin_tok = _login(c, "admin", "admin123")
    ah = {"Authorization": f"Bearer {admin_tok}"}
    assert c.get("/api/scheduler/jobs", headers=ah).status_code == 200
    # cron preview 仍对登录用户开放
    assert c.get("/api/scheduler/cron/preview", headers=h, params={"cron": "0 * * * *"}).status_code == 200


def test_admin_integration_honors_system_integration_codes():
    c, _ = _client()
    tok = _login(c, "analyst", "analyst123")
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/admin/integration/dolphin", headers=h).status_code == 403

    admin_tok = _login(c, "admin", "admin123")
    ah = {"Authorization": f"Bearer {admin_tok}"}
    assert c.get("/api/admin/integration/dolphin", headers=ah).status_code == 200

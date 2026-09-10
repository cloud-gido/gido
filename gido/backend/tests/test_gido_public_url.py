# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""平台集成「站点入口」覆盖 GIDO_PUBLIC_URL。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-site-url")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models import rbac_models  # noqa: F401
from app.models.rbac_models import Role
from app.models.workspace import User, Workspace, WorkspaceMember
from app.services.alert_notification import gido_public_url, instance_ops_url, normalize_gido_public_url
from app.services.ds_runtime import ensure_platform_integration_row
from app.services.rbac_seed import run_rbac_bootstrap
from app.api import admin_integration, auth


def test_normalize_public_url():
    assert normalize_gido_public_url(" https://gido.example.com/ ") == "https://gido.example.com"
    assert normalize_gido_public_url("") is None
    try:
        normalize_gido_public_url("gido.example.com", require_http=True)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_db_overrides_env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    monkeypatch.setattr(settings, "GIDO_PUBLIC_URL", "http://from-env:8080")
    assert gido_public_url(db) == "http://from-env:8080"
    row = ensure_platform_integration_row(db)
    row.gido_public_url = "https://gido.prod.example"
    db.flush()
    assert gido_public_url(db) == "https://gido.prod.example"
    assert "operation?workspace_id=3" in (instance_ops_url(3, 9, db) or "")
    row.gido_public_url = None
    db.flush()
    assert gido_public_url(db) == "http://from-env:8080"
    db.close()


def test_admin_can_plug_site_url():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
    ws = Workspace(name="infras", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    db.commit()
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(admin_integration.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    c = TestClient(app)

    analyst_tok = c.post("/api/auth/login", json={"username": "analyst", "password": "analyst123"}).json()["access_token"]
    assert c.get("/api/admin/integration/site", headers={"Authorization": f"Bearer {analyst_tok}"}).status_code == 403

    admin_tok = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).json()["access_token"]
    ah = {"Authorization": f"Bearer {admin_tok}"}
    r = c.put("/api/admin/integration/site", headers=ah, json={"gido_public_url": "https://gido.example.com/"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["effective_url"] == "https://gido.example.com"
    assert body["effective_source"] == "database"
    assert body["deep_link_example"].endswith("/gido/batch/operation?workspace_id=1&instance=1")

    bad = c.put("/api/admin/integration/site", headers=ah, json={"gido_public_url": "gido.example.com"})
    assert bad.status_code == 400

    cleared = c.put("/api/admin/integration/site", headers=ah, json={"gido_public_url": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["effective_source"] in ("environment", "empty")
    assert not cleared.json()["override_url"]

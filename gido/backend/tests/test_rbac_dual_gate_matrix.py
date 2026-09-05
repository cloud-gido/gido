# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""双门禁矩阵：平台角色 × 空间角色 × 模块读路径。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-rbac-matrix")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.rbac_models import Role
from app.models.workspace import DataSource, User, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.api import auth, datasource, probe, studio, copilot


def _build():
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

    users = {}
    specs = [
        ("pa", "platform_admin", "admin123", True),
        ("dev", "developer", "dev123", False),
        ("analyst", "analyst", "analyst123", False),
        ("ops", "operator", "ops123", False),
    ]
    for uname, rcode, pwd, is_admin in specs:
        u = User(
            username=uname,
            email=f"{uname}@gido.com",
            hashed_password=get_password_hash(pwd),
            is_admin=is_admin,
            is_active=True,
            role_id=roles[rcode].id,
        )
        db.add(u)
        db.flush()
        users[uname] = (u, pwd)
    db.commit()
    for uname, (u, _) in users.items():
        db.refresh(u)

    ws = Workspace(name="matrix", owner_id=users["pa"][0].id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)

    # 每人两个成员视角：developer / viewer 通过不同成员行无法共存，这里固定：
    # pa=admin, dev=developer, analyst=viewer, ops=developer
    membership = {
        "pa": "admin",
        "dev": "developer",
        "analyst": "viewer",
        "ops": "developer",
    }
    for uname, role in membership.items():
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=users[uname][0].id, role=role))
    ds = DataSource(
        workspace_id=ws.id,
        name="doris",
        ds_type="doris",
        host="127.0.0.1",
        port=9030,
        database="demo",
        username="root",
        password="",
        is_active=True,
        created_by=users["pa"][0].id,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    ds_id = ds.id
    ws_id = ws.id
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(datasource.router, prefix="/api")
    app.include_router(probe.router, prefix="/api")
    app.include_router(studio.router, prefix="/api")
    app.include_router(copilot.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), users, ws_id, ds_id


def _tok(c: TestClient, uname: str, pwd: str) -> dict:
    r = c.post("/api/auth/login", json={"username": uname, "password": pwd})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_dual_gate_matrix_read_paths():
    c, users, ws_id, ds_id = _build()

    # platform admin：全通
    h = _tok(c, "pa", users["pa"][1])
    assert c.get("/api/datasources", headers=h, params={"workspace_id": ws_id}).status_code == 200
    assert c.get(f"/api/datasources/{ds_id}", headers=h).status_code == 200

    # developer + 空间 developer：studio / ds OK
    h = _tok(c, "dev", users["dev"][1])
    assert c.get("/api/studio/nodes", headers=h, params={"workspace_id": ws_id}).status_code in (200, 404)
    # 404/200 视路由而定；至少不是 403
    studio = c.get("/api/studio/nodes", headers=h, params={"workspace_id": ws_id})
    assert studio.status_code != 403, studio.text

    # analyst + 空间 viewer：list/get datasource OK；studio 403
    h = _tok(c, "analyst", users["analyst"][1])
    assert c.get("/api/datasources", headers=h, params={"workspace_id": ws_id}).status_code == 200
    assert c.get(f"/api/datasources/{ds_id}", headers=h).status_code == 200
    studio = c.get("/api/studio/nodes", headers=h, params={"workspace_id": ws_id})
    assert studio.status_code == 403

    # copilot：非成员空间应 403
    foreign_ws = 999999
    chat = c.post(
        "/api/copilot/chat",
        headers=h,
        json={"workspace_id": foreign_ws, "message": "hi"},
    )
    assert chat.status_code in (403, 404)

    # 本空间 status 允许（已是成员）
    st = c.get("/api/copilot/status", headers=h, params={"workspace_id": ws_id})
    assert st.status_code == 200

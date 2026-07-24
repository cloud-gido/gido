# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
完整特性回归（内存 SQLite + TestClient，不连真实集群）：
- 脚本增删改查
- 静默草稿 vs 保存版本（历史）
- 双人编辑锁 / 抢锁 / 无锁禁止改
- 断网草稿回灌（本地草稿 → 恢复后 PUT）
- Stream 作业草稿与版本历史
"""
from __future__ import annotations

import os

# 必须在任何 app.* 导入前设置（避免绑到本机 Postgres）
os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-autosave")

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
import app.api.streaming  # noqa: F401 — 注册 StreamingJob 表
from app.models import rbac_models  # noqa: F401
from app.api import auth, workspace, studio, streaming


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
    app.include_router(streaming.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _login(c: TestClient, username: str, password: str) -> str:
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _setup_collab_workspace(client: TestClient):
    admin = _login(client, "admin", "admin123")
    for u, e in (("alice", "alice@gido.test"), ("bob", "bob@gido.test")):
        r = client.post(
            "/api/auth/register",
            headers=_h(admin),
            json={"username": u, "email": e, "password": "password123", "full_name": u},
        )
        assert r.status_code == 200, r.text

    alice = _login(client, "alice", "password123")
    bob = _login(client, "bob", "password123")
    alice_id = client.get("/api/auth/me", headers=_h(alice)).json()["id"]
    bob_id = client.get("/api/auth/me", headers=_h(bob)).json()["id"]

    ws = client.post(
        "/api/workspaces",
        headers=_h(admin),
        json={"name": "ws-autosave-itest", "description": "collab"},
    )
    assert ws.status_code == 200, ws.text
    ws_id = ws.json()["id"]
    for uid in (alice_id, bob_id):
        r = client.post(
            f"/api/workspaces/{ws_id}/members",
            headers=_h(admin),
            json={"user_id": uid, "role": "developer"},
        )
        assert r.status_code == 200, r.text
    return admin, alice, bob, ws_id


def test_studio_crud_draft_history_and_delete(client: TestClient):
    _, alice, _, ws_id = _setup_collab_workspace(client)

    # Create
    created = client.post(
        "/api/studio/nodes",
        headers=_h(alice),
        json={
            "workspace_id": ws_id,
            "name": "sql_demo",
            "node_type": "SQL",
            "script_content": "select 1",
        },
    )
    assert created.status_code == 200, created.text
    nid = created.json()["id"]
    base = {"workspace_id": ws_id, "name": "sql_demo", "node_type": "SQL"}

    # Read
    got = client.get(f"/api/studio/nodes/{nid}", headers=_h(alice))
    assert got.status_code == 200
    assert got.json()["script_content"] == "select 1"

    # Update draft (no history)
    d1 = client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(alice),
        params={"create_history": False},
        json={**base, "script_content": "select 2"},
    )
    assert d1.status_code == 200, d1.text
    hist0 = client.get(f"/api/studio/nodes/{nid}/history", headers=_h(alice))
    assert hist0.status_code == 200
    assert len(hist0.json()) == 0

    # Explicit version save → history of previous non-empty script
    v1 = client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(alice),
        params={"create_history": True},
        json={**base, "script_content": "select 3"},
    )
    assert v1.status_code == 200, v1.text
    hist1 = client.get(f"/api/studio/nodes/{nid}/history", headers=_h(alice)).json()
    assert len(hist1) >= 1
    assert any("select 2" in (h.get("script_content") or "") for h in hist1)

    # List
    listed = client.get("/api/studio/nodes", headers=_h(alice), params={"workspace_id": ws_id})
    assert listed.status_code == 200
    assert any(n["id"] == nid for n in listed.json())

    # Delete
    deleted = client.delete(f"/api/studio/nodes/{nid}", headers=_h(alice))
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/studio/nodes/{nid}", headers=_h(alice)).status_code == 404


def test_multiuser_edit_lock_steal_and_forbidden_write(client: TestClient):
    _, alice, bob, ws_id = _setup_collab_workspace(client)

    node = client.post(
        "/api/studio/nodes",
        headers=_h(alice),
        json={
            "workspace_id": ws_id,
            "name": "lock_demo",
            "node_type": "SQL",
            "script_content": "select 1",
        },
    ).json()
    nid = node["id"]
    base = {"workspace_id": ws_id, "name": "lock_demo", "node_type": "SQL"}

    # Alice acquires
    acq = client.post(f"/api/studio/nodes/{nid}/acquire-edit-lock", headers=_h(alice))
    assert acq.status_code == 200, acq.text

    # Bob cannot acquire without force
    assert client.post(f"/api/studio/nodes/{nid}/acquire-edit-lock", headers=_h(bob)).status_code == 409

    # Bob cannot draft-save while Alice holds lock
    denied = client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(bob),
        params={"create_history": False},
        json={**base, "script_content": "select bob"},
    )
    assert denied.status_code == 403, denied.text

    # Alice can draft
    ok = client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(alice),
        params={"create_history": False},
        json={**base, "script_content": "select alice"},
    )
    assert ok.status_code == 200, ok.text

    # Bob steals
    steal = client.post(
        f"/api/studio/nodes/{nid}/acquire-edit-lock",
        headers=_h(bob),
        params={"force": True},
    )
    assert steal.status_code == 200, steal.text

    # Alice now forbidden
    denied2 = client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(alice),
        params={"create_history": False},
        json={**base, "script_content": "select alice2"},
    )
    assert denied2.status_code == 403, denied2.text

    # Bob writes then releases
    assert client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(bob),
        params={"create_history": False},
        json={**base, "script_content": "select bob-ok"},
    ).status_code == 200
    assert client.post(f"/api/studio/nodes/{nid}/release-edit-lock", headers=_h(bob)).status_code == 200

    got = client.get(f"/api/studio/nodes/{nid}", headers=_h(alice)).json()
    assert got["script_content"] == "select bob-ok"


def test_offline_local_draft_reconnect_put(client: TestClient):
    """模拟断网：本地保留草稿，恢复网络后 create_history=false 回灌。"""
    _, alice, _, ws_id = _setup_collab_workspace(client)
    node = client.post(
        "/api/studio/nodes",
        headers=_h(alice),
        json={
            "workspace_id": ws_id,
            "name": "offline_demo",
            "node_type": "SQL",
            "script_content": "select online",
        },
    ).json()
    nid = node["id"]
    assert client.post(f"/api/studio/nodes/{nid}/acquire-edit-lock", headers=_h(alice)).status_code == 200

    # 「断网」期间只写本地（内存模拟 localStorage）
    local_store = {}
    key = f"gido.scriptDraft.v1.studio.{ws_id}.{nid}"
    local_store[key] = {"script": "select offline-edit", "updatedAt": 1}

    # 「恢复网络」：若本地 ≠ 服务端则 PUT 草稿
    server = client.get(f"/api/studio/nodes/{nid}", headers=_h(alice)).json()["script_content"]
    draft = local_store[key]["script"]
    assert draft != server
    put = client.put(
        f"/api/studio/nodes/{nid}",
        headers=_h(alice),
        params={"create_history": False},
        json={
            "workspace_id": ws_id,
            "name": "offline_demo",
            "node_type": "SQL",
            "script_content": draft,
        },
    )
    assert put.status_code == 200, put.text
    assert client.get(f"/api/studio/nodes/{nid}", headers=_h(alice)).json()["script_content"] == draft
    # 草稿回灌不产生历史
    assert len(client.get(f"/api/studio/nodes/{nid}/history", headers=_h(alice)).json()) == 0


def test_stream_draft_vs_version_history(client: TestClient):
    _, alice, _, ws_id = _setup_collab_workspace(client)
    job = client.post(
        "/api/streaming/jobs",
        headers=_h(alice),
        json={
            "workspace_id": ws_id,
            "name": "job-autosave-1",
            "job_type": "SQL",
            "script_content": "SELECT 1",
        },
    )
    assert job.status_code == 200, job.text
    jid = job.json()["id"]

    draft = client.put(
        f"/api/streaming/jobs/{jid}",
        headers=_h(alice),
        params={"create_history": False},
        json={"script_content": "SELECT 2"},
    )
    assert draft.status_code == 200, draft.text
    hist0 = client.get(f"/api/streaming/jobs/{jid}/history", headers=_h(alice))
    assert hist0.status_code == 200
    assert len(hist0.json()) == 0

    ver = client.put(
        f"/api/streaming/jobs/{jid}",
        headers=_h(alice),
        params={"create_history": True},
        json={"script_content": "SELECT 3"},
    )
    assert ver.status_code == 200, ver.text
    hist1 = client.get(f"/api/streaming/jobs/{jid}/history", headers=_h(alice)).json()
    assert len(hist1) >= 1

    # 删除作业（查）
    assert client.delete(f"/api/streaming/jobs/{jid}", headers=_h(alice)).status_code == 200


def test_repeated_identical_draft_is_noop(client: TestClient):
    _, alice, _, ws_id = _setup_collab_workspace(client)
    node = client.post(
        "/api/studio/nodes",
        headers=_h(alice),
        json={
            "workspace_id": ws_id,
            "name": "noop_demo",
            "node_type": "SQL",
            "script_content": "select same",
        },
    ).json()
    nid = node["id"]
    base = {"workspace_id": ws_id, "name": "noop_demo", "node_type": "SQL", "script_content": "select same"}
    for _ in range(3):
        r = client.put(
            f"/api/studio/nodes/{nid}",
            headers=_h(alice),
            params={"create_history": False},
            json=base,
        )
        assert r.status_code == 200
    assert len(client.get(f"/api/studio/nodes/{nid}/history", headers=_h(alice)).json()) == 0

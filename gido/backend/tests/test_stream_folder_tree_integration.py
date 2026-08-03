# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
Stream 目录树集成：挪目录 / 排目录 / 挪作业 / 排作业。

对齐 test_studio_folder_tree_integration：只改 folder_id / sort_order / parent_id，
不改脚本内容、类型、作业 id。
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-stream-folder-tree")

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
    ws_id = wss.json()[0]["id"]
    return token, ws_id


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_stream_folder_reparent_reorder_and_job_move_preserve_script(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)

    f1 = client.post(
        "/api/streaming/folders",
        headers=h,
        json={"workspace_id": ws_id, "name": "dir_a", "parent_id": None},
    )
    assert f1.status_code == 200, f1.text
    fa = f1.json()["id"]

    f2 = client.post(
        "/api/streaming/folders",
        headers=h,
        json={"workspace_id": ws_id, "name": "dir_b", "parent_id": None},
    )
    assert f2.status_code == 200, f2.text
    fb = f2.json()["id"]

    rr = client.put(
        "/api/streaming/folders/reorder",
        headers=h,
        json={"workspace_id": ws_id, "parent_id": None, "folder_ids": [fb, fa]},
    )
    assert rr.status_code == 200, rr.text

    listed = client.get("/api/streaming/folders", headers=h, params={"workspace_id": ws_id})
    assert listed.status_code == 200, listed.text
    by_id = {x["id"]: x for x in listed.json()}
    assert by_id[fb]["sort_order"] < by_id[fa]["sort_order"]

    mv = client.patch(
        f"/api/streaming/folders/{fa}/parent",
        headers=h,
        json={"parent_id": fb},
    )
    assert mv.status_code == 200, mv.text
    assert mv.json()["parent_id"] == fb

    cycle = client.patch(
        f"/api/streaming/folders/{fb}/parent",
        headers=h,
        json={"parent_id": fa},
    )
    assert cycle.status_code == 400

    script = "SELECT /* stream-dev */ 42 AS x"
    j1 = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-sql-a",
            "job_type": "SQL",
            "script_content": script,
            "folder_id": None,
        },
    )
    assert j1.status_code == 200, j1.text
    jid = j1.json()["id"]
    assert j1.json()["script_content"] == script

    j2 = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-sql-b",
            "job_type": "SQL",
            "script_content": "select 2",
            "folder_id": None,
        },
    )
    assert j2.status_code == 200, j2.text
    jid2 = j2.json()["id"]

    # 创建到目录内
    j3 = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-sql-in-folder",
            "job_type": "SQL",
            "script_content": "select 3",
            "folder_id": fb,
        },
    )
    assert j3.status_code == 200, j3.text
    assert j3.json()["folder_id"] == fb

    moved = client.patch(
        f"/api/streaming/jobs/{jid}/folder",
        headers=h,
        json={"folder_id": fb},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["folder_id"] == fb
    assert moved.json()["id"] == jid
    assert moved.json()["script_content"] == script
    assert moved.json()["job_type"] == "SQL"

    client.patch(f"/api/streaming/jobs/{jid2}/folder", headers=h, json={"folder_id": fb})
    ord_r = client.put(
        "/api/streaming/jobs/reorder",
        headers=h,
        json={"workspace_id": ws_id, "folder_id": fb, "job_ids": [jid2, jid]},
    )
    assert ord_r.status_code == 200, ord_r.text

    got_list = client.get("/api/streaming/jobs", headers=h, params={"workspace_id": ws_id})
    assert got_list.status_code == 200
    body = next(x for x in got_list.json() if x["id"] == jid)
    assert body["id"] == jid
    assert body["folder_id"] == fb
    assert body["script_content"] == script
    assert body["job_type"] == "SQL"
    assert body["name"] == "job-sql-a"

    so = {x["id"]: x.get("sort_order", 0) for x in got_list.json() if x["id"] in (jid, jid2)}
    assert so[jid2] < so[jid]


def test_stream_reorder_rejects_cross_parent_folders(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    fa = client.post(
        "/api/streaming/folders", headers=h, json={"workspace_id": ws_id, "name": "p", "parent_id": None}
    ).json()["id"]
    fb = client.post(
        "/api/streaming/folders", headers=h, json={"workspace_id": ws_id, "name": "c", "parent_id": fa}
    ).json()["id"]
    bad = client.put(
        "/api/streaming/folders/reorder",
        headers=h,
        json={"workspace_id": ws_id, "parent_id": None, "folder_ids": [fa, fb]},
    )
    assert bad.status_code == 400


def test_stream_nested_folder_move_out_to_root_then_reorder(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    parent = client.post(
        "/api/streaming/folders", headers=h, json={"workspace_id": ws_id, "name": "outer", "parent_id": None}
    ).json()["id"]
    child = client.post(
        "/api/streaming/folders", headers=h, json={"workspace_id": ws_id, "name": "inner", "parent_id": parent}
    ).json()["id"]

    assert (
        client.put(
            "/api/streaming/folders/reorder",
            headers=h,
            json={"workspace_id": ws_id, "parent_id": None, "folder_ids": [child, parent]},
        ).status_code
        == 400
    )

    out = client.patch(f"/api/streaming/folders/{child}/parent", headers=h, json={"parent_id": None})
    assert out.status_code == 200, out.text
    assert out.json()["parent_id"] is None

    ok = client.put(
        "/api/streaming/folders/reorder",
        headers=h,
        json={"workspace_id": ws_id, "parent_id": None, "folder_ids": [child, parent]},
    )
    assert ok.status_code == 200, ok.text

    listed = client.get("/api/streaming/folders", headers=h, params={"workspace_id": ws_id})
    by_id = {x["id"]: x for x in listed.json()}
    assert by_id[child]["parent_id"] is None
    assert by_id[parent]["parent_id"] is None
    assert by_id[child]["sort_order"] < by_id[parent]["sort_order"]


def test_stream_delete_folder_moves_jobs_to_root(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    folder = client.post(
        "/api/streaming/folders", headers=h, json={"workspace_id": ws_id, "name": "tmp", "parent_id": None}
    ).json()["id"]
    job = client.post(
        "/api/streaming/jobs",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job-to-unfile",
            "job_type": "SQL",
            "script_content": "select 1",
            "folder_id": folder,
        },
    )
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    assert job.json()["folder_id"] == folder

    deleted = client.delete(f"/api/streaming/folders/{folder}", headers=h)
    assert deleted.status_code == 200, deleted.text

    listed = client.get("/api/streaming/jobs", headers=h, params={"workspace_id": ws_id})
    assert listed.status_code == 200
    body = next(x for x in listed.json() if x["id"] == jid)
    assert body["folder_id"] is None
    assert body["script_content"] == "select 1"

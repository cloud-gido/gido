# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
Studio 目录树集成：挪目录 / 排目录 / 挪脚本 / 排脚本。

断言：只改 folder_id / sort_order / parent_id，不改脚本内容、类型、节点 id。
工作流绑的是 node_id，目录组织变更不影响调度绑定字段。
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-folder-tree")

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
    ws_id = wss.json()[0]["id"]
    return token, ws_id


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_folder_reparent_reorder_and_node_move_preserve_script(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)

    f1 = client.post(
        "/api/studio/folders",
        headers=h,
        json={"workspace_id": ws_id, "name": "dir_a", "parent_id": None},
    )
    assert f1.status_code == 200, f1.text
    fa = f1.json()["id"]

    f2 = client.post(
        "/api/studio/folders",
        headers=h,
        json={"workspace_id": ws_id, "name": "dir_b", "parent_id": None},
    )
    assert f2.status_code == 200, f2.text
    fb = f2.json()["id"]

    # 同级目录排序：b 在前
    rr = client.put(
        "/api/studio/folders/reorder",
        headers=h,
        json={"workspace_id": ws_id, "parent_id": None, "folder_ids": [fb, fa]},
    )
    assert rr.status_code == 200, rr.text

    listed = client.get("/api/studio/folders", headers=h, params={"workspace_id": ws_id})
    assert listed.status_code == 200, listed.text
    by_id = {x["id"]: x for x in listed.json()}
    assert by_id[fb]["sort_order"] < by_id[fa]["sort_order"]

    # 把 a 挂到 b 下
    mv = client.patch(
        f"/api/studio/folders/{fa}/parent",
        headers=h,
        json={"parent_id": fb},
    )
    assert mv.status_code == 200, mv.text
    assert mv.json()["parent_id"] == fb

    # 禁止成环：b 不能挂到 a（a 是 b 的子）
    cycle = client.patch(
        f"/api/studio/folders/{fb}/parent",
        headers=h,
        json={"parent_id": fa},
    )
    assert cycle.status_code == 400

    script = "SELECT /* batch-dev */ 42 AS x"
    n1 = client.post(
        "/api/studio/nodes",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job_sql",
            "node_type": "SQL",
            "script_content": script,
            "folder_id": None,
        },
    )
    assert n1.status_code == 200, n1.text
    nid = n1.json()["id"]
    assert n1.json()["script_content"] == script

    n2 = client.post(
        "/api/studio/nodes",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "job_sql_2",
            "node_type": "SQL",
            "script_content": "select 2",
            "folder_id": None,
        },
    )
    assert n2.status_code == 200, n2.text
    nid2 = n2.json()["id"]

    # 挪脚本进目录
    moved = client.patch(
        f"/api/studio/nodes/{nid}/folder",
        headers=h,
        json={"folder_id": fb},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["folder_id"] == fb
    assert moved.json()["id"] == nid
    assert moved.json()["script_content"] == script
    assert moved.json()["node_type"] == "SQL"

    # 再挪第二个进同目录并排序
    client.patch(f"/api/studio/nodes/{nid2}/folder", headers=h, json={"folder_id": fb})
    ord_r = client.put(
        "/api/studio/nodes/reorder",
        headers=h,
        json={"workspace_id": ws_id, "folder_id": fb, "node_ids": [nid2, nid]},
    )
    assert ord_r.status_code == 200, ord_r.text

    got = client.get(f"/api/studio/nodes/{nid}", headers=h)
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == nid
    assert body["folder_id"] == fb
    assert body["script_content"] == script
    assert body["node_type"] == "SQL"
    assert body["name"] == "job_sql"

    listed_nodes = client.get(
        "/api/studio/nodes",
        headers=h,
        params={"workspace_id": ws_id, "folder_id": fb},
    )
    assert listed_nodes.status_code == 200
    ids = [x["id"] for x in listed_nodes.json()]
    # 列表可能含其它节点；至少两节点 sort_order 反映 reorder
    so = {x["id"]: x.get("sort_order", 0) for x in listed_nodes.json() if x["id"] in (nid, nid2)}
    assert so[nid2] < so[nid]


def test_reorder_rejects_cross_parent_folders(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    fa = client.post(
        "/api/studio/folders", headers=h, json={"workspace_id": ws_id, "name": "p", "parent_id": None}
    ).json()["id"]
    fb = client.post(
        "/api/studio/folders", headers=h, json={"workspace_id": ws_id, "name": "c", "parent_id": fa}
    ).json()["id"]
    bad = client.put(
        "/api/studio/folders/reorder",
        headers=h,
        json={"workspace_id": ws_id, "parent_id": None, "folder_ids": [fa, fb]},
    )
    assert bad.status_code == 400

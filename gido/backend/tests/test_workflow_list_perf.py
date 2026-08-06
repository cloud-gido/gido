# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作流列表：分页摘要契约（无 dag_config）+ 批量组装。"""
from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-workflow-list")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.workflow import (
    _dag_node_count,
    workflows_to_list_items,
    workflows_to_out_list,
)
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.workspace import (
    JobVersion,
    PublishApproval,
    User,
    Workflow,
    Workspace,
    WorkspaceMember,
)
from app.services.rbac_seed import run_rbac_bootstrap
import app.api.streaming  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.api import auth, workspace, workflow


def _wf(**kwargs):
    base = dict(
        id=1,
        workspace_id=9,
        name="demo",
        description=None,
        dag_config={"nodes": [{"id": 1}, {"id": 2}], "edges": [], "ds_meta": {"needs_republish": True}},
        schedule_type="cron",
        cron_expression="0 2 * * *",
        is_active=True,
        created_at=datetime(2026, 1, 1),
        updated_at=None,
        created_by=11,
        updated_by=12,
        status="published",
        active_version_id=101,
        scheduler_engine="dolphin",
        scheduler_definition_id="9001",
        scheduler_project_id="77",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_dag_node_count():
    assert _dag_node_count(None) == 0
    assert _dag_node_count({"nodes": [1, 2, 3]}) == 3
    assert _dag_node_count({"nodes": "x"}) == 0


def test_workflows_to_out_list_batches_lookups():
    wf_a = _wf(id=1, name="a", created_by=11, updated_by=12, active_version_id=101)
    wf_b = _wf(id=2, name="b", created_by=11, updated_by=None, active_version_id=102, scheduler_definition_id=None)

    user_q = MagicMock()
    user_q.filter.return_value.all.return_value = [
        SimpleNamespace(id=11, username="alice"),
        SimpleNamespace(id=12, username="bob"),
    ]
    ver_q = MagicMock()
    ver_q.filter.return_value.all.return_value = [
        SimpleNamespace(id=101, workflow_id=1, version_no=3, status="active"),
        SimpleNamespace(id=102, workflow_id=2, version_no=1, status="active"),
    ]

    db = MagicMock()

    def query_side_effect(model):
        if model is User:
            return user_q
        if model is JobVersion:
            return ver_q
        raise AssertionError(f"unexpected query model: {model}")

    db.query.side_effect = query_side_effect

    runtime = SimpleNamespace(
        enabled=True,
        url="http://ds.example/dolphinscheduler",
        ui_url="http://ds.example/dolphinscheduler/ui",
    )

    with patch("app.api.workflow.get_dolphin_runtime", return_value=runtime) as rt:
        out = workflows_to_out_list([wf_a, wf_b], db, workspace_id=9)

    rt.assert_called_once_with(db, 9)
    assert len(out) == 2
    assert out[0].created_by_username == "alice"
    assert out[0].active_version_no == 3
    assert db.query.call_count == 2


def test_workflows_to_list_items_strips_dag_and_marks_pending():
    wf = _wf(id=5)
    out = SimpleNamespace(
        model_dump=lambda: {
            "id": 5,
            "workspace_id": 9,
            "name": "demo",
            "description": None,
            "dag_config": {"nodes": [1, 2]},
            "schedule_type": "manual",
            "cron_expression": None,
            "is_active": True,
            "created_at": datetime(2026, 1, 1),
            "updated_at": None,
            "created_by": 11,
            "created_by_username": "alice",
            "updated_by": None,
            "updated_by_username": None,
            "status": "published",
            "active_version_id": None,
            "active_version_no": 1,
            "scheduler_engine": "dolphin",
            "scheduler_definition_id": "1",
            "scheduler_project_id": "2",
            "dolphin_workflow_url": None,
            "needs_ds_republish": False,
        }
    )
    db = MagicMock()
    with patch("app.api.workflow.workflows_to_out_list", return_value=[out]), patch(
        "app.api.workflow._prefetch_pending_publish", return_value={5}
    ):
        items = workflows_to_list_items([wf], db, workspace_id=9)
    assert len(items) == 1
    assert items[0].node_count == 2
    assert items[0].pending_publish is True
    assert not hasattr(items[0], "dag_config") or "dag_config" not in items[0].model_dump()


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

    for i, (name, status) in enumerate(
        [("online-a", "published"), ("online-b", "published"), ("draft-x", "draft"), ("off-y", "offline")],
        start=1,
    ):
        db.add(
            Workflow(
                workspace_id=ws0.id,
                name=name,
                description=f"desc-{name}",
                dag_config={"nodes": [{"id": j} for j in range(i)], "edges": []},
                schedule_type="manual",
                status=status,
                is_active=True,
                created_by=admin.id,
                updated_by=admin.id,
            )
        )
    db.commit()
    published = db.query(Workflow).filter(Workflow.name == "online-a").first()
    db.add(
        PublishApproval(
            workspace_id=ws0.id,
            resource_type="workflow",
            resource_id=published.id,
            action="publish_to_ds",
            status="pending",
            submitted_by=admin.id,
        )
    )
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
    app.include_router(workspace.router, prefix="/api")
    app.include_router(workflow.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c


def _login(client: TestClient) -> dict:
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_list_workflows_default_published_summary(client: TestClient):
    h = _login(client)
    r = client.get("/api/workflows", params={"workspace_id": 1}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"items", "total", "page", "page_size", "creators"}
    assert body["total"] == 2
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert "dag_config" not in item
        assert "node_count" in item
        assert item["status"] == "published"
    pending_row = next(x for x in body["items"] if x["name"] == "online-a")
    assert pending_row["pending_publish"] is True
    assert any(c["username"] == "admin" for c in body["creators"])


def test_list_workflows_status_all_and_keyword_paging(client: TestClient):
    h = _login(client)
    r = client.get(
        "/api/workflows",
        params={"workspace_id": 1, "status": "all", "page": 1, "page_size": 2},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["page_size"] == 2

    r2 = client.get(
        "/api/workflows",
        params={"workspace_id": 1, "status": "all", "keyword": "draft"},
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["total"] == 1
    assert body2["items"][0]["name"] == "draft-x"


def test_get_workflow_includes_dag_config(client: TestClient):
    h = _login(client)
    listed = client.get("/api/workflows", params={"workspace_id": 1, "status": "all"}, headers=h).json()
    wf_id = listed["items"][0]["id"]
    r = client.get(f"/api/workflows/{wf_id}", headers=h)
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "dag_config" in detail
    assert isinstance(detail["dag_config"].get("nodes"), list)

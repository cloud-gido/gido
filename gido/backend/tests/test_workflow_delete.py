# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""删除工作流：级联清理本地实例/版本，并同步删除 Dolphin 流程定义。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-workflow-delete")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.workspace import (
    AlertEvent,
    BackfillRequest,
    JobVersion,
    NodeInstance,
    PublishApproval,
    TaskNode,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
    WorkspaceMember,
)
from app.services.rbac_seed import run_rbac_bootstrap
import app.api.streaming  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.api import auth, workspace, workflow


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
    app.include_router(workflow.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        c.SessionLocal = SessionLocal  # type: ignore[attr-defined]
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


def test_delete_workflow_purges_local_dependents_and_calls_ds(client: TestClient, monkeypatch):
    token, ws_id = _login(client)
    h = _h(token)
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    db = SessionLocal()

    node = TaskNode(workspace_id=ws_id, name="n1", node_type="SHELL", script_content="echo 1")
    db.add(node)
    db.commit()
    db.refresh(node)

    wf = Workflow(
        workspace_id=ws_id,
        name="to-delete",
        dag_config={"nodes": [{"node_id": node.id}], "edges": []},
        schedule_type="cron",
        cron_expression="0 0 * * *",
        status="offline",
        scheduler_engine="dolphin",
        scheduler_project_id="100",
        scheduler_definition_id="200",
        created_by=1,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)

    ver = JobVersion(
        workflow_id=wf.id,
        version_no=1,
        dag_snapshot=wf.dag_config,
        cron_snapshot=wf.cron_expression,
        schedule_type_snapshot="cron",
        status="active",
        scheduler_definition_id="200",
        scheduler_project_id="100",
    )
    db.add(ver)
    db.commit()
    db.refresh(ver)
    wf.active_version_id = ver.id
    db.commit()

    bf = BackfillRequest(
        workflow_id=wf.id,
        job_version_id=ver.id,
        date_start="2026-01-01",
        date_end="2026-01-01",
        status="success",
        total_instances=1,
    )
    db.add(bf)
    db.commit()
    db.refresh(bf)

    inst = WorkflowInstance(
        workflow_id=wf.id,
        job_version_id=ver.id,
        backfill_request_id=bf.id,
        status="success",
        trigger_type="schedule",
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)

    ni = NodeInstance(workflow_instance_id=inst.id, node_id=node.id, status="success")
    db.add(ni)
    db.commit()
    db.refresh(ni)

    db.add(
        AlertEvent(
            workspace_id=ws_id,
            workflow_id=wf.id,
            workflow_instance_id=inst.id,
            node_instance_id=ni.id,
            alert_type="failed",
            message="x",
        )
    )
    db.add(
        PublishApproval(
            workspace_id=ws_id,
            resource_type="workflow",
            resource_id=wf.id,
            resource_name=wf.name,
            action="publish_to_ds",
            status="cancelled",
            submitted_by=1,
        )
    )
    db.commit()
    wf_id = wf.id
    node_id = node.id
    db.close()

    deleted: list[tuple[int, int]] = []

    class _FakeDs:
        def delete_process_definition(self, project_code: int, process_code: int) -> None:
            deleted.append((project_code, process_code))

    monkeypatch.setattr("app.services.dolphin.ds_client", _FakeDs())
    monkeypatch.setattr("app.api.workflow.refresh_ds_client", lambda *a, **k: None)

    r = client.delete(f"/api/workflows/{wf_id}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("dolphin_deleted") is True
    assert deleted == [(100, 200)]

    db = SessionLocal()
    assert db.query(Workflow).filter(Workflow.id == wf_id).first() is None
    assert db.query(JobVersion).filter(JobVersion.workflow_id == wf_id).count() == 0
    assert db.query(WorkflowInstance).filter(WorkflowInstance.workflow_id == wf_id).count() == 0
    assert db.query(BackfillRequest).filter(BackfillRequest.workflow_id == wf_id).count() == 0
    assert db.query(AlertEvent).filter(AlertEvent.workflow_id == wf_id).count() == 0
    assert (
        db.query(PublishApproval)
        .filter(PublishApproval.resource_type == "workflow", PublishApproval.resource_id == wf_id)
        .count()
        == 0
    )
    # 脚本节点保留
    assert db.query(TaskNode).filter(TaskNode.id == node_id).first() is not None
    db.close()


def test_delete_published_workflow_blocked(client: TestClient):
    token, ws_id = _login(client)
    h = _h(token)
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    db = SessionLocal()
    wf = Workflow(
        workspace_id=ws_id,
        name="online",
        dag_config={"nodes": [], "edges": []},
        status="published",
        scheduler_project_id="1",
        scheduler_definition_id="2",
        created_by=1,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    wf_id = wf.id
    db.close()

    r = client.delete(f"/api/workflows/{wf_id}", headers=h)
    assert r.status_code == 400, r.text
    assert "下线" in r.json()["detail"]

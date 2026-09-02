# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""发布审批资源预览：只读快照 + 基准对比。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-approval-preview")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.data_service import DataApi
from app.models.workspace import DataSource, JobVersion, User, Workflow, Workspace, WorkspaceMember
from app.services.publish_approval import get_publish_approval_preview, submit_publish_approval
from app.services.rbac_seed import run_rbac_bootstrap
import app.api.studio  # noqa: F401
import app.api.streaming  # noqa: F401
import app.api.data_service  # noqa: F401
import app.api.approval  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.api import auth, workspace, studio, workflow, approval, data_service
from app.api.streaming import StreamingJob, create_streaming_job_release


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
    dev = User(
        username="dev",
        email="dev@gido.com",
        full_name="开发",
        hashed_password=get_password_hash("dev123"),
        is_admin=False,
        is_active=True,
    )
    db.add_all([admin, dev])
    db.commit()
    db.refresh(admin)
    db.refresh(dev)
    ws0 = Workspace(name="infras", description="default", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws0)
    db.commit()
    db.refresh(ws0)
    db.add_all([
        WorkspaceMember(workspace_id=ws0.id, user_id=admin.id, role="admin"),
        WorkspaceMember(workspace_id=ws0.id, user_id=dev.id, role="developer"),
    ])
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
    app.include_router(workflow.router, prefix="/api")
    app.include_router(approval.router, prefix="/api")
    app.include_router(data_service.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        yield c, SessionLocal
    app.dependency_overrides.clear()


def _login(c: TestClient, username="admin", password="admin123") -> tuple[str, int]:
    passwords = {"admin": "admin123", "dev": "dev123"}
    r = c.post("/api/auth/login", json={"username": username, "password": passwords[username]})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    wss = c.get("/api/workspaces", headers={"Authorization": f"Bearer {token}"})
    ws_list = wss.json()
    ws_id = ws_list[0]["id"] if ws_list else 1
    return token, ws_id


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_approval_preview_studio_node(client):
    c, SessionLocal = client
    token, ws_id = _login(c)
    h = _auth_headers(token)

    created = c.post(
        "/api/studio/nodes",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "ads_demo",
            "node_type": "SQL",
            "script_content": "SELECT 1",
        },
    )
    assert created.status_code == 200
    nid = created.json()["id"]

    db = SessionLocal()
    try:
        dev = db.query(User).filter(User.username == "dev").first()
        row = submit_publish_approval(
            db, dev, ws_id, "studio_node", nid, "publish_node", "请审批脚本"
        )
        preview = get_publish_approval_preview(db, dev, row.id)
    finally:
        db.close()

    assert preview["preview"]["kind"] == "studio_node"
    assert preview["preview"]["pending"]["script_content"] == "SELECT 1"
    assert preview["approval"]["submit_note"] == "请审批脚本"

    api_prev = c.get(f"/api/approvals/{row.id}/preview", headers=h)
    assert api_prev.status_code == 200
    assert api_prev.json()["preview"]["pending"]["script_content"] == "SELECT 1"


def test_approval_preview_workflow_with_baseline(client):
    c, SessionLocal = client
    token, ws_id = _login(c)
    h = _auth_headers(token)

    dag_pending = {
        "nodes": [{"node_id": 1, "name": "n1", "node_type": "SQL"}],
        "edges": [],
    }
    created = c.post(
        "/api/workflows",
        headers=h,
        json={
            "workspace_id": ws_id,
            "name": "wf_preview",
            "schedule_type": "cron",
            "cron_expression": "0 0 * * *",
            "dag_config": dag_pending,
        },
    )
    assert created.status_code == 200, created.text
    wf_id = created.json()["id"]

    db = SessionLocal()
    try:
        dev = db.query(User).filter(User.username == "dev").first()
        wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
        base_ver = JobVersion(
            workflow_id=wf.id,
            version_no=1,
            dag_snapshot={"nodes": [], "edges": []},
            cron_snapshot="0 1 * * *",
            schedule_type_snapshot="cron",
            status="active",
            published_by=dev.id,
        )
        db.add(base_ver)
        db.flush()
        wf.active_version_id = base_ver.id
        db.commit()

        row = submit_publish_approval(
            db, dev, ws_id, "workflow", wf_id, "publish_to_ds", "工作流变更"
        )
        preview = get_publish_approval_preview(db, dev, row.id)
    finally:
        db.close()

    assert preview["preview"]["kind"] == "workflow"
    assert preview["preview"]["pending"]["dag"]["node_count"] == 1
    assert preview["preview"]["baseline"] is not None
    assert preview["preview"]["baseline_label"] == "生产版本 v1"
    assert preview["preview"]["has_diff"] is True

    api_prev = c.get(f"/api/approvals/{row.id}/preview", headers=h)
    assert api_prev.status_code == 200
    body = api_prev.json()
    assert body["preview"]["pending"]["cron_expression"] == "0 0 * * *"


def test_approval_preview_stream_job_with_release_diff(client):
    c, SessionLocal = client
    token, ws_id = _login(c, username="dev", password="dev123")
    h = _auth_headers(token)

    db = SessionLocal()
    try:
        dev = db.query(User).filter(User.username == "dev").first()
        job = StreamingJob(
            workspace_id=ws_id,
            name="stream-preview",
            job_type="SQL",
            script_content="SELECT draft",
            parallelism=1,
            flink_sql_submit_mode="session",
            flink_jar_submit_mode="session",
            status="draft",
            lifecycle_state="draft",
            created_by=dev.id,
            owner_id=dev.id,
            is_locked=False,
        )
        db.add(job)
        db.flush()

        approved = create_streaming_job_release(
            db, job, dev.id, script_content="SELECT approved_v1", release_note="v1"
        )
        approved.approval_status = "approved"
        pending = create_streaming_job_release(
            db, job, dev.id, script_content="SELECT pending_v2", release_note="v2"
        )
        db.commit()

        row = submit_publish_approval(
            db, dev, ws_id, "stream_job", job.id, "submit_job", "实时发布"
        )
        assert row.release_id == pending.id
        preview = get_publish_approval_preview(db, dev, row.id)
    finally:
        db.close()

    assert preview["preview"]["kind"] == "stream_job"
    assert preview["preview"]["pending"]["script_content"] == "SELECT pending_v2"
    assert preview["preview"]["baseline"]["script_content"] == "SELECT approved_v1"
    assert preview["preview"]["has_diff"] is True

    api_prev = c.get(f"/api/approvals/{row.id}/preview", headers=h)
    assert api_prev.status_code == 200
    assert api_prev.json()["preview"]["pending"]["release_version"] == pending.version


def test_approval_preview_data_service_api_pending_diff(client):
    c, SessionLocal = client
    token, ws_id = _login(c)
    h = _auth_headers(token)

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        dev = db.query(User).filter(User.username == "dev").first()
        ds = DataSource(
            workspace_id=ws_id,
            name="doris_preview",
            ds_type="doris",
            host="127.0.0.1",
            port=9030,
        )
        db.add(ds)
        db.flush()
        api = DataApi(
            workspace_id=ws_id,
            api_code="preview_api",
            name="Preview API",
            mode="sql",
            http_method="GET",
            status="online",
            version=1,
            datasource_id=ds.id,
            sql_template="SELECT 1",
            pending_definition={"sql_template": "SELECT 2"},
            created_by=admin.id,
        )
        db.add(api)
        db.commit()
        db.refresh(api)

        row = submit_publish_approval(
            db, dev, ws_id, "data_service_api", api.id, "publish_api", "API 变更"
        )
        preview = get_publish_approval_preview(db, dev, row.id)
    finally:
        db.close()

    assert preview["preview"]["kind"] == "data_service_api"
    assert preview["preview"]["pending"]["sql_template"] == "SELECT 2"
    assert preview["preview"]["baseline"]["sql_template"] == "SELECT 1"
    assert preview["preview"]["has_diff"] is True

    api_prev = c.get(f"/api/approvals/{row.id}/preview", headers=h)
    assert api_prev.status_code == 200


def test_approval_preview_not_found(client):
    c, _SessionLocal = client
    token, _ws_id = _login(c)
    h = _auth_headers(token)
    r = c.get("/api/approvals/99999/preview", headers=h)
    assert r.status_code == 404


def test_approval_preview_forbidden_cross_workspace(client):
    c, SessionLocal = client
    dev_token, _ws_id = _login(c, username="dev", password="dev123")
    dev_h = _auth_headers(dev_token)

    db = SessionLocal()
    try:
        from app.models.workspace import PublishApproval, TaskNode

        admin = db.query(User).filter(User.username == "admin").first()
        ws2 = Workspace(name="isolated", description="other", owner_id=admin.id, timezone="Asia/Shanghai")
        db.add(ws2)
        db.flush()
        db.add(WorkspaceMember(workspace_id=ws2.id, user_id=admin.id, role="admin"))
        node = TaskNode(
            workspace_id=ws2.id,
            name="secret_node",
            node_type="SQL",
            script_content="SELECT secret",
        )
        db.add(node)
        db.flush()
        row = PublishApproval(
            workspace_id=ws2.id,
            resource_type="studio_node",
            resource_id=node.id,
            resource_name=node.name,
            action="publish_node",
            status="pending",
            submitted_by=admin.id,
        )
        db.add(row)
        db.commit()
        approval_id = row.id
    finally:
        db.close()

    r = c.get(f"/api/approvals/{approval_id}/preview", headers=dev_h)
    assert r.status_code == 403

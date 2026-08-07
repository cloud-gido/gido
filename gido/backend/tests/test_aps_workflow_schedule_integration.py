# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""APS 工作流定时防双跑：集成测试（内存 SQLite + TestClient，不连真实 Dolphin）。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("APS_WORKFLOW_SCHEDULE_ENABLED", "")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-aps-integration")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.workspace import (
    PlatformIntegration,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
    WorkspaceMember,
    WorkspacePlatformIntegration,
)
from app.services.rbac_seed import run_rbac_bootstrap
import app.api.streaming  # noqa: F401
from app.models import rbac_models  # noqa: F401
from app.api import auth, admin_integration
from app.services import scheduler as svc_scheduler
from app.services.scheduler import _run_workflow_job_unlocked, reload_schedules


def _clear_aps_wf_jobs():
    for job in list(svc_scheduler.scheduler.get_jobs()):
        if str(job.id).startswith("wf_"):
            job.remove()


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
    ws = Workspace(name="infras", description="default", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    db.add(PlatformIntegration(id=1))
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
    app.include_router(admin_integration.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    # 绑定与 API 相同的 SessionLocal，供调度服务查库
    import app.core.database as dbmod

    prev_session = dbmod.SessionLocal
    dbmod.SessionLocal = SessionLocal

    if not svc_scheduler.scheduler.running:
        svc_scheduler.scheduler.start(paused=True)
    _clear_aps_wf_jobs()

    with TestClient(app) as c:
        c.SessionLocal = SessionLocal  # type: ignore[attr-defined]
        c.ws_id = ws.id  # type: ignore[attr-defined]
        yield c

    _clear_aps_wf_jobs()
    dbmod.SessionLocal = prev_session
    app.dependency_overrides.clear()


def _login(c: TestClient) -> str:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_workflows(SessionLocal, ws_id: int):
    db = SessionLocal()
    local = Workflow(
        workspace_id=ws_id,
        name="local-aps-only",
        dag_config={"nodes": [], "edges": []},
        schedule_type="cron",
        cron_expression="0 2 * * *",
        is_active=True,
        status="draft",
        # 模型默认也是 dolphin，不能据此判托管
        scheduler_engine="dolphin",
        scheduler_definition_id=None,
    )
    managed = Workflow(
        workspace_id=ws_id,
        name="bigdata-checklist",
        dag_config={"nodes": [], "edges": []},
        schedule_type="cron",
        cron_expression="30 8 * * *",
        is_active=True,
        status="published",
        scheduler_engine="dolphin",
        scheduler_definition_id="9001",
    )
    db.add_all([local, managed])
    db.commit()
    db.refresh(local)
    db.refresh(managed)
    ids = (local.id, managed.id)
    db.close()
    return ids


def test_reload_skips_ds_managed_and_registers_local(client: TestClient):
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    ws_id = client.ws_id  # type: ignore[attr-defined]
    local_id, managed_id = _seed_workflows(SessionLocal, ws_id)

    reload_schedules()
    wf_jobs = {j.id: j for j in svc_scheduler.scheduler.get_jobs() if str(j.id).startswith("wf_")}
    assert f"wf_{local_id}" in wf_jobs
    assert f"wf_{managed_id}" not in wf_jobs


def test_disable_api_clears_aps_workflow_jobs(client: TestClient):
    token = _login(client)
    h = _h(token)
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    ws_id = client.ws_id  # type: ignore[attr-defined]
    local_id, _managed_id = _seed_workflows(SessionLocal, ws_id)
    reload_schedules()
    assert any(j.id == f"wf_{local_id}" for j in svc_scheduler.scheduler.get_jobs())

    st = client.get("/api/admin/integration/aps-workflow-schedule", headers=h)
    assert st.status_code == 200, st.text
    assert st.json()["aps_workflow_job_count"] >= 1

    r = client.post("/api/admin/integration/aps-workflow-schedule/disable", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "force_off"
    assert body["aps_workflow_job_count"] == 0
    assert body["db_override"] is False
    assert not any(str(j.id).startswith("wf_") for j in svc_scheduler.scheduler.get_jobs())

    db = SessionLocal()
    row = db.query(PlatformIntegration).filter(PlatformIntegration.id == 1).one()
    assert row.aps_workflow_schedule_enabled is False
    db.close()


def test_run_unlocked_skips_ds_managed_workflow(client: TestClient):
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    ws_id = client.ws_id  # type: ignore[attr-defined]
    _local_id, managed_id = _seed_workflows(SessionLocal, ws_id)

    _run_workflow_job_unlocked(managed_id)

    db = SessionLocal()
    n = db.query(WorkflowInstance).filter(WorkflowInstance.workflow_id == managed_id).count()
    db.close()
    assert n == 0


def test_run_unlocked_skips_after_force_disable(client: TestClient):
    token = _login(client)
    h = _h(token)
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    ws_id = client.ws_id  # type: ignore[attr-defined]
    local_id, _ = _seed_workflows(SessionLocal, ws_id)

    r = client.post("/api/admin/integration/aps-workflow-schedule/disable", headers=h)
    assert r.status_code == 200, r.text

    _run_workflow_job_unlocked(local_id)
    db = SessionLocal()
    n = db.query(WorkflowInstance).filter(WorkflowInstance.workflow_id == local_id).count()
    db.close()
    assert n == 0


def test_workspace_ds_enabled_blocks_aps_registration(client: TestClient):
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    ws_id = client.ws_id  # type: ignore[attr-defined]
    db = SessionLocal()
    db.add(
        WorkspacePlatformIntegration(
            workspace_id=ws_id,
            ds_enabled=True,
            ds_url="http://ds.example/dolphinscheduler",
            ds_token="t",
        )
    )
    wf = Workflow(
        workspace_id=ws_id,
        name="ws-ds-cron",
        dag_config={"nodes": [], "edges": []},
        schedule_type="cron",
        cron_expression="15 9 * * *",
        is_active=True,
        status="draft",
        scheduler_definition_id=None,
        scheduler_engine=None,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    wf_id = wf.id
    db.close()

    reload_schedules()
    assert f"wf_{wf_id}" not in {j.id for j in svc_scheduler.scheduler.get_jobs()}


def test_put_restore_auto_reregisters_local(client: TestClient):
    token = _login(client)
    h = _h(token)
    SessionLocal = client.SessionLocal  # type: ignore[attr-defined]
    ws_id = client.ws_id  # type: ignore[attr-defined]
    local_id, managed_id = _seed_workflows(SessionLocal, ws_id)

    assert client.post("/api/admin/integration/aps-workflow-schedule/disable", headers=h).status_code == 200
    assert not any(str(j.id).startswith("wf_") for j in svc_scheduler.scheduler.get_jobs())

    r = client.put("/api/admin/integration/aps-workflow-schedule", headers=h, json={"enabled": None})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "auto"
    jobs = {j.id for j in svc_scheduler.scheduler.get_jobs() if str(j.id).startswith("wf_")}
    assert f"wf_{local_id}" in jobs
    assert f"wf_{managed_id}" not in jobs

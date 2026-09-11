# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
被前端轮询的只读接口不能同步调执行引擎。

踩过的坑：实例中心概览、实例列表、告警列表都在请求里「兜底采集一轮」，
而冷却时间只在采集成功时才记。引擎一慢，每 15 秒的轮询都重新去打它，
请求各自占着一个 DB 会话挂到网关超时（524），连接池被吃干之后
连纯读的告警中心都打不开。采集是后台 scheduler_instance_poll 的事。
"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-no-engine")

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import alert, auth, operation
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models import rbac_models  # noqa: F401
from app.models.rbac_models import Role
from app.models.workspace import (
    AlertEvent,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
    WorkspaceMember,
)
from app.services.rbac_seed import run_rbac_bootstrap


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    run_rbac_bootstrap(db)
    roles = {r.code: r for r in db.query(Role).all()}
    admin = User(
        username="admin", email="admin@gido.com", hashed_password=get_password_hash("admin123"),
        is_admin=True, is_active=True, role_id=roles["platform_admin"].id,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    ws = Workspace(name="infras", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    wf = Workflow(name="ads_daily", workspace_id=ws.id, status="published", is_active=True)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="failed", trigger_type="schedule",
        business_date="2026-09-10", started_at=datetime(2026, 9, 11, 1, 0),
    ))
    db.add(AlertEvent(
        workspace_id=ws.id, workflow_id=wf.id, alert_type="failed", level="error",
        status="open", message="ads_daily 运行失败", dedupe_key="wf:1:biz:2026-09-10",
        notification_status="sent",
    ))
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
    app.include_router(operation.router, prefix="/api")
    app.include_router(alert.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return c, {"Authorization": f"Bearer {r.json()['access_token']}"}, ws_id


@pytest.fixture(autouse=True)
def engine_is_a_tripwire(monkeypatch):
    """任何一次对引擎的同步采集/HTTP 调用都直接让测试失败。"""
    def _boom(*_a, **_kw):
        raise AssertionError("轮询接口里不该调用执行引擎")

    import requests

    monkeypatch.setattr("app.services.run_collector.collect_runs", _boom, raising=False)
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "put", _boom)


def test_overview_is_read_only(client):
    c, h, ws_id = client
    r = c.get("/api/operation/overview", headers=h, params={"workspace_id": ws_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["failed"] == 1
    # 采集是否落后由 collector 字段告诉前端，而不是靠请求里现采一轮
    assert "collector" in body


def test_instance_list_is_read_only(client):
    c, h, ws_id = client
    r = c.get("/api/operation/instances", headers=h, params={"workspace_id": ws_id, "page": 1})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


def test_alert_list_is_read_only(client):
    c, h, ws_id = client
    r = c.get("/api/alerts", headers=h, params={"workspace_id": ws_id})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


def test_repeated_polling_stays_read_only(client):
    """
    冷却时间原先只在采集成功时才记，所以引擎挂着的时候每一轮轮询都会重新去打它。
    这里连打几轮，确认没有任何一轮溜去调引擎。
    """
    c, h, ws_id = client
    for _ in range(4):
        assert c.get("/api/operation/overview", headers=h, params={"workspace_id": ws_id}).status_code == 200
        assert c.get("/api/alerts", headers=h, params={"workspace_id": ws_id}).status_code == 200

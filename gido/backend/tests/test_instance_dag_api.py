# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""实例 DAG：回看历史实例要看到当时那张图，而不是被改过的当前编排。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-dag")

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, operation
from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models import rbac_models  # noqa: F401
from app.models.rbac_models import Role
from app.models.workspace import (
    JobVersion,
    NodeInstance,
    TaskNode,
    User,
    Workflow,
    WorkflowInstance,
    Workspace,
    WorkspaceMember,
)
from app.services.rbac_seed import run_rbac_bootstrap

_NOW = datetime(2026, 9, 11, 4, 0, 0)


def _client():
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

    a = TaskNode(workspace_id=ws.id, name="ods_load", node_type="SQL")
    b = TaskNode(workspace_id=ws.id, name="dwd_order", node_type="SQL")
    c = TaskNode(workspace_id=ws.id, name="ads_report", node_type="SQL")
    db.add_all([a, b, c])
    db.commit()
    for n in (a, b, c):
        db.refresh(n)

    version = JobVersion(
        workflow_id=wf.id, version_no=3, status="active",
        dag_snapshot={
            "nodes": [
                {"node_id": a.id, "name": "ods_load", "node_type": "SQL", "ds_task_code": "9001"},
                {"node_id": b.id, "name": "dwd_order", "node_type": "SQL", "ds_task_code": "9002"},
                {"node_id": c.id, "name": "ads_report", "node_type": "SQL", "ds_task_code": "9003"},
            ],
            "edges": [{"source": a.id, "target": b.id}, {"source": b.id, "target": c.id}],
        },
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    wf.active_version_id = version.id
    # 发布后在 GIDO 里改了名，并且把图删到只剩一个节点
    a.name = "ods_load_v2"
    wf.dag_config = {"nodes": [{"node_id": a.id, "name": "ods_load_v2"}], "edges": []}

    inst = WorkflowInstance(
        workflow_id=wf.id, job_version_id=version.id, status="failed",
        trigger_type="schedule", business_date="2026-09-10",
        started_at=_NOW - timedelta(hours=2), finished_at=_NOW - timedelta(hours=1),
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    # a 成功，b 失败，c 压根没跑到
    db.add(NodeInstance(
        workflow_instance_id=inst.id, node_id=a.id, status="success",
        started_at=_NOW - timedelta(hours=2), finished_at=_NOW - timedelta(hours=2) + timedelta(seconds=90),
    ))
    db.add(NodeInstance(
        workflow_instance_id=inst.id, node_id=b.id, status="failed",
        started_at=_NOW - timedelta(hours=1, minutes=30), finished_at=_NOW - timedelta(hours=1),
        log_content="ERROR: table not found\nstack...", retry_count=2,
    ))
    db.commit()

    ids = {
        "ws": int(ws.id), "wf": int(wf.id), "inst": int(inst.id),
        "a": int(a.id), "b": int(b.id), "c": int(c.id),
    }
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
    app.dependency_overrides[get_db] = _get_db
    return TestClient(app), SessionLocal, ids


def _auth(c: TestClient) -> dict:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _dag(c, h, ids):
    r = c.get(
        f"/api/operation/workflows/{ids['wf']}/instances/{ids['inst']}/dag",
        headers=h, params={"workspace_id": ids["ws"]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_dag_comes_from_the_version_snapshot_not_current_definition():
    """当前编排只剩 1 个节点，但这次运行的图有 3 个——必须返回 3 个。"""
    c, _S, ids = _client()
    body = _dag(c, _auth(c), ids)

    assert [n["node_id"] for n in body["nodes"]] == [ids["a"], ids["b"], ids["c"]]
    assert body["edges"] == [
        {"source": ids["a"], "target": ids["b"]},
        {"source": ids["b"], "target": ids["c"]},
    ]
    assert body["instance"]["version_no"] == 3


def test_node_names_are_the_published_ones():
    """图上用发布时的名字，这样和引擎日志里的任务名对得上；现用名单独给出。"""
    c, _S, ids = _client()
    body = _dag(c, _auth(c), ids)
    a = next(n for n in body["nodes"] if n["node_id"] == ids["a"])
    assert a["name"] == "ods_load"
    assert a["current_name"] == "ods_load_v2"


def test_node_statuses_reflect_this_run():
    c, _S, ids = _client()
    body = _dag(c, _auth(c), ids)
    by_id = {n["node_id"]: n for n in body["nodes"]}

    assert by_id[ids["a"]]["status"] == "success"
    assert by_id[ids["a"]]["duration_seconds"] == 90
    assert by_id[ids["b"]]["status"] == "failed"
    assert by_id[ids["b"]]["retry_count"] == 2
    assert by_id[ids["b"]]["log_summary"] == "ERROR: table not found"


def test_node_never_reached_is_not_run_not_pending():
    """快照里有但这次没走到的节点，得是「未运行」，不能伪装成「等待中」。"""
    c, _S, ids = _client()
    body = _dag(c, _auth(c), ids)
    c_node = next(n for n in body["nodes"] if n["node_id"] == ids["c"])
    assert c_node["status"] == "not_run"
    assert c_node["node_instance_id"] is None
    assert c_node["duration_seconds"] is None


def test_instance_from_another_workflow_is_rejected():
    c, SessionLocal, ids = _client()
    h = _auth(c)
    db = SessionLocal()
    other = Workflow(name="other_wf", workspace_id=ids["ws"], status="published")
    db.add(other)
    db.commit()
    db.refresh(other)
    other_id = int(other.id)
    db.close()

    r = c.get(
        f"/api/operation/workflows/{other_id}/instances/{ids['inst']}/dag",
        headers=h, params={"workspace_id": ids["ws"]},
    )
    assert r.status_code == 404


def test_falls_back_to_current_definition_without_a_version():
    """老实例没绑版本时退回当前定义，至少还能看出个图。"""
    c, SessionLocal, ids = _client()
    h = _auth(c)
    db = SessionLocal()
    inst = WorkflowInstance(
        workflow_id=ids["wf"], status="success", trigger_type="manual",
        business_date="2026-09-09",
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    legacy_id = int(inst.id)
    db.close()

    r = c.get(
        f"/api/operation/workflows/{ids['wf']}/instances/{legacy_id}/dag",
        headers=h, params={"workspace_id": ids["ws"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["instance"]["version_no"] is None
    assert [n["name"] for n in body["nodes"]] == ["ods_load_v2"]


def test_dag_carries_the_sync_signal():
    """
    节点明细表格被运行图取代后，原先只在表格上下文里的「最近同步 / 采集报错」必须跟过来：
    图上的状态可不可信，全看这两个字段。
    """
    c, SessionLocal, ids = _client()
    h = _auth(c)
    db = SessionLocal()
    inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ids["inst"]).first()
    inst.last_synced_at = _NOW - timedelta(minutes=3)
    inst.scheduler_error = "拉取任务列表超时"
    db.commit()
    db.close()

    body = _dag(c, h, ids)
    assert body["instance"]["last_synced_at"] is not None
    assert body["instance"]["scheduler_error"] == "拉取任务列表超时"


def test_node_instance_list_endpoint_is_gone():
    """节点明细表格与运行图重复，列表接口已随之移除，别再留着没人用的入口。"""
    c, _S, ids = _client()
    r = c.get("/api/operation/node-instances", headers=_auth(c), params={"workspace_id": ids["ws"]})
    assert r.status_code == 404

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""实例热账本留存：只留最近 N 天终态，历史以 Dolphin 为准。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-retention")

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import AlertEvent, NodeInstance, TaskNode, Workflow, WorkflowInstance, Workspace
from app.services.instance_retention import purge_expired_workflow_instances


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    ws = Workspace(name="infras", timezone="Asia/Shanghai")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    wf = Workflow(name="ads", workspace_id=ws.id, status="published")
    session.add(wf)
    session.commit()
    session.refresh(wf)
    node = TaskNode(name="sql1", workspace_id=ws.id, node_type="sql")
    session.add(node)
    session.commit()
    session.refresh(node)
    yield session, ws, wf, node
    session.close()


def test_purge_skips_when_retention_disabled(db, monkeypatch):
    session, _ws, wf, _node = db
    monkeypatch.setattr("app.core.config.settings.INSTANCE_RETENTION_DAYS", 0)
    old = WorkflowInstance(
        workflow_id=wf.id,
        status="success",
        finished_at=datetime.utcnow() - timedelta(days=90),
        created_at=datetime.utcnow() - timedelta(days=90),
    )
    session.add(old)
    session.commit()
    out = purge_expired_workflow_instances(session, retention_days=0)
    assert out["enabled"] is False
    assert session.query(WorkflowInstance).count() == 1


def test_purge_keeps_recent_and_running(db, monkeypatch):
    session, ws, wf, node = db
    monkeypatch.setattr("app.core.config.settings.INSTANCE_RETENTION_DAYS", 7)
    keep_ok = WorkflowInstance(
        workflow_id=wf.id,
        status="success",
        finished_at=datetime.utcnow() - timedelta(days=2),
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    keep_run = WorkflowInstance(
        workflow_id=wf.id,
        status="running",
        started_at=datetime.utcnow() - timedelta(days=40),
        created_at=datetime.utcnow() - timedelta(days=40),
    )
    drop = WorkflowInstance(
        workflow_id=wf.id,
        status="failed",
        finished_at=datetime.utcnow() - timedelta(days=10),
        created_at=datetime.utcnow() - timedelta(days=10),
    )
    session.add_all([keep_ok, keep_run, drop])
    session.commit()
    session.refresh(drop)
    session.add(NodeInstance(workflow_instance_id=drop.id, node_id=node.id, status="failed"))
    session.add(
        AlertEvent(
            workspace_id=ws.id,
            workflow_id=wf.id,
            workflow_instance_id=drop.id,
            alert_type="failed",
            level="error",
            status="open",
            message="old fail",
        )
    )
    session.commit()

    out = purge_expired_workflow_instances(session, retention_days=7, batch_size=100)
    assert out["deleted_instances"] == 1
    assert out["deleted_nodes"] == 1
    assert out["deleted_alerts"] == 1
    left = {i.status for i in session.query(WorkflowInstance).all()}
    assert left == {"success", "running"}


def test_purge_stops_on_time_budget(db, monkeypatch):
    """墙钟预算到点就停，避免一次清理拖住业务连接。"""
    session, _ws, wf, _node = db
    monkeypatch.setattr("app.core.config.settings.INSTANCE_RETENTION_DAYS", 7)
    for i in range(30):
        session.add(
            WorkflowInstance(
                workflow_id=wf.id,
                status="success",
                created_at=datetime.utcnow() - timedelta(days=20, seconds=i),
                finished_at=datetime.utcnow() - timedelta(days=20, seconds=i),
            )
        )
    session.commit()
    out = purge_expired_workflow_instances(
        session,
        retention_days=7,
        batch_size=5,
        max_batches=100,
        time_budget_ms=1,
        pause_ms=0,
    )
    assert out["stopped_reason"] == "time_budget"
    assert out["deleted_instances"] < 30
    assert session.query(WorkflowInstance).count() > 0


def test_purge_breaks_parent_instance_link(db, monkeypatch):
    session, _ws, wf, _node = db
    monkeypatch.setattr("app.core.config.settings.INSTANCE_RETENTION_DAYS", 7)
    parent = WorkflowInstance(
        workflow_id=wf.id,
        status="failed",
        finished_at=datetime.utcnow() - timedelta(days=20),
        created_at=datetime.utcnow() - timedelta(days=20),
    )
    session.add(parent)
    session.commit()
    session.refresh(parent)
    child = WorkflowInstance(
        workflow_id=wf.id,
        status="success",
        parent_instance_id=parent.id,
        finished_at=datetime.utcnow() - timedelta(days=1),
        created_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(child)
    session.commit()
    parent_id = int(parent.id)
    child_id = int(child.id)

    purge_expired_workflow_instances(session, retention_days=7)
    session.expire_all()
    child = session.query(WorkflowInstance).filter(WorkflowInstance.id == child_id).one()
    assert child.parent_instance_id is None
    assert session.query(WorkflowInstance).filter(WorkflowInstance.id == parent_id).first() is None

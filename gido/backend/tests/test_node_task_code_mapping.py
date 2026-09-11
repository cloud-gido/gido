# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""节点实例按发布时的 task code 对齐引擎任务：改名、改图都不该让节点明细整片消失。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-task-code")

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    JobVersion,
    NodeInstance,
    TaskNode,
    Workflow,
    WorkflowInstance,
    Workspace,
)
from app.services.dolphin_instance_sync import (
    _node_resolver,
    _upsert_node_instances_from_ds_tasks,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def _fixture(db, *, published_name: str, current_name: str, with_task_code: bool = True):
    """发布时叫 published_name，之后在 GIDO 里改名为 current_name。"""
    ws = Workspace(name="infras", timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    wf = Workflow(name="ads_daily", workspace_id=ws.id, status="published", is_active=True)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    node = TaskNode(workspace_id=ws.id, name=current_name, node_type="SQL")
    db.add(node)
    db.commit()
    db.refresh(node)

    published_node = {"node_id": node.id, "name": published_name}
    if with_task_code:
        published_node["ds_task_code"] = "9001"
    version = JobVersion(
        workflow_id=wf.id, version_no=1, status="active",
        dag_snapshot={"nodes": [published_node], "edges": []},
        scheduler_project_id="777", scheduler_definition_id="888",
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    # 当前定义已经是改名后的样子，且不带 task code（模拟改名后未重新发布）
    wf.dag_config = {"nodes": [{"node_id": node.id, "name": current_name}], "edges": []}
    wf.active_version_id = version.id
    inst = WorkflowInstance(
        workflow_id=wf.id, job_version_id=version.id, status="failed",
        trigger_type="schedule", scheduler_engine="dolphin",
        scheduler_project_id="777", scheduler_definition_id="888",
        scheduler_instance_id="123", started_at=datetime.utcnow(),
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return wf, inst, node


def _engine_task(name: str, *, code: str = "9001", state: str = "FAILURE") -> dict:
    return {"id": 555, "name": name, "taskCode": code, "state": state}


def test_renamed_node_still_matches_by_task_code(db):
    """引擎里还是发布时的旧名字，GIDO 已改名——按 task code 仍要对上。"""
    wf, inst, node = _fixture(db, published_name="ods_load", current_name="ods_load_v2")

    resolver = _node_resolver(db, inst, wf)
    changed, touched = _upsert_node_instances_from_ds_tasks(
        db, inst, [_engine_task("ods_load")], resolver, "Asia/Shanghai"
    )
    db.commit()

    assert touched == 1, "改名后按 task code 应该仍能对上"
    rows = db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all()
    assert len(rows) == 1
    assert rows[0].node_id == node.id
    assert rows[0].scheduler_task_code == "9001"
    assert rows[0].status == "failed"


def test_name_fallback_for_versions_published_without_task_code(db):
    """老版本快照里没有 task code，按名字兜底不能丢。"""
    wf, inst, node = _fixture(
        db, published_name="ods_load", current_name="ods_load", with_task_code=False
    )

    _changed, touched = _upsert_node_instances_from_ds_tasks(
        db, inst, [_engine_task("ods_load", code="9001")], _node_resolver(db, inst, wf), "Asia/Shanghai"
    )
    db.commit()

    assert touched == 1
    row = db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).first()
    assert row.node_id == node.id
    # 引擎给的 code 顺带回填，下次就能走 code 匹配
    assert row.scheduler_task_code == "9001"


def test_task_code_wins_over_a_colliding_name(db):
    """名字撞车时以 task code 为准，不能把状态写到另一个节点上。"""
    wf, inst, node = _fixture(db, published_name="ods_load", current_name="ods_load")
    other = TaskNode(workspace_id=wf.workspace_id, name="ods_load", node_type="SQL")
    db.add(other)
    db.commit()
    db.refresh(other)
    snapshot = dict(wf.dag_config)
    # 两个节点同名，但只有 node 带着发布时的 task code
    version = db.query(JobVersion).filter(JobVersion.workflow_id == wf.id).first()
    version.dag_snapshot = {
        "nodes": [
            {"node_id": node.id, "name": "ods_load", "ds_task_code": "9001"},
            {"node_id": other.id, "name": "ods_load", "ds_task_code": "9002"},
        ],
        "edges": [],
    }
    db.commit()

    _changed, touched = _upsert_node_instances_from_ds_tasks(
        db, inst, [_engine_task("ods_load", code="9002")], _node_resolver(db, inst, wf), "Asia/Shanghai"
    )
    db.commit()

    assert touched == 1
    row = db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).first()
    assert row.node_id == other.id, "应该按 code 落到 9002 对应的节点"
    assert snapshot  # 保留引用，说明当前定义未参与判定


def test_resolver_uses_version_snapshot_not_current_definition(db):
    """当前定义被改图删掉了这个节点，历史实例的节点明细仍要能解析出来。"""
    wf, inst, node = _fixture(db, published_name="ods_load", current_name="ods_load")
    wf.dag_config = {"nodes": [], "edges": []}
    db.commit()

    resolver = _node_resolver(db, inst, wf)
    assert resolver.by_code == {"9001": node.id}
    assert resolver.resolve(_engine_task("ods_load")) == node.id


def test_unmatched_task_is_skipped_not_misassigned(db):
    """引擎里多出来的任务（code 和名字都对不上）应当跳过，不能塞给任意节点。"""
    wf, inst, _node = _fixture(db, published_name="ods_load", current_name="ods_load_v2")

    _changed, touched = _upsert_node_instances_from_ds_tasks(
        db, inst, [_engine_task("some_other_task", code="7777")], _node_resolver(db, inst, wf), "Asia/Shanghai"
    )
    db.commit()

    assert touched == 0
    assert db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).count() == 0

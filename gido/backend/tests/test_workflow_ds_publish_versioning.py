# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.workspace import (
    JobVersion,
    TaskNode,
    Workflow,
    Workspace,
)
from app.services.rbac_seed import migrate_job_version_publishing


def _fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    workspace = Workspace(name="publish-ws")
    db.add(workspace)
    db.flush()
    node = TaskNode(
        workspace_id=workspace.id,
        name="python",
        node_type="PYTHON",
        script_content="print('frozen source')",
    )
    db.add(node)
    db.flush()
    workflow = Workflow(
        workspace_id=workspace.id,
        name="workflow",
        dag_config={"nodes": [{"node_id": node.id}], "edges": []},
        scheduler_engine="dolphin",
    )
    db.add(workflow)
    db.flush()
    active = JobVersion(
        workflow_id=workflow.id,
        version_no=1,
        dag_snapshot={"nodes": [{"node_id": node.id, "script_content": "print('old')"}]},
        status="active",
    )
    db.add(active)
    db.flush()
    workflow.active_version_id = active.id
    db.commit()
    return db, workflow.id, active.id, node.id


def _patch_publish(monkeypatch, engine):
    import app.services.workflow_ds_publish as publish

    monkeypatch.setattr(
        publish, "get_dolphin_runtime", lambda *_: SimpleNamespace(enabled=True)
    )
    monkeypatch.setattr(publish, "refresh_ds_client", lambda *_: None)
    monkeypatch.setattr(publish, "validate_workflow_publishable", lambda *_: None)
    monkeypatch.setattr(publish, "get_scheduler_engine", lambda *_: engine)
    monkeypatch.setattr(
        publish,
        "dolphin_workflow_console_url",
        lambda *_args, **_kwargs: "http://ds/workflow",
    )
    return publish


def _definition(node_id):
    return SimpleNamespace(
        engine="dolphin",
        project_id="11",
        definition_id="22",
        definition_version=3,
        task_sync=[
            {
                "node_id": node_id,
                "node_type": "PYTHON",
                "ds_task_type": "SHELL",
                "execution_mode": "callback",
                "ds_task_code": 33,
            }
        ],
    )


def test_callback_publish_uses_versioned_source(monkeypatch):
    db, workflow_id, old_active_id, node_id = _fixture()

    class Engine:
        def publish_definition(self, workflow, **_kwargs):
            node = workflow.dag_config["nodes"][0]
            assert node["script_content"] == "print('frozen source')"
            assert "python_artifact" not in node
            assert "python_runtime" not in node
            return _definition(node_id)

        def online_definition(self, *_args):
            return None

    publish = _patch_publish(monkeypatch, Engine())
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).one()
    result = publish.publish_workflow_to_ds(db, workflow)

    assert result["version_no"] == 2
    assert result["scheduler_definition_version"] == 3
    assert (
        db.query(JobVersion).filter(JobVersion.id == old_active_id).one().status
        == "archived"
    )
    version = db.query(JobVersion).filter(JobVersion.id == result["job_version_id"]).one()
    assert version.status == "active"
    assert "python_artifact" not in version.dag_snapshot["nodes"][0]


def test_publish_failure_preserves_old_active_version_and_dag(monkeypatch):
    db, workflow_id, old_active_id, _node_id = _fixture()
    original_dag = deepcopy(
        db.query(Workflow).filter(Workflow.id == workflow_id).one().dag_config
    )

    class Engine:
        def publish_definition(self, *_args, **_kwargs):
            raise RuntimeError("Dolphin unavailable")

    publish = _patch_publish(monkeypatch, Engine())
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).one()
    with pytest.raises(RuntimeError, match="Dolphin unavailable"):
        publish.publish_workflow_to_ds(db, workflow)

    db.expire_all()
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).one()
    assert workflow.active_version_id == old_active_id
    assert workflow.dag_config == original_dag
    assert db.query(JobVersion).filter(JobVersion.id == old_active_id).one().status == "active"
    failed = (
        db.query(JobVersion)
        .filter(JobVersion.workflow_id == workflow_id, JobVersion.status == "failed")
        .one()
    )
    assert failed.build_finished_at is not None
    assert failed.build_error == "Dolphin unavailable"


def test_concurrent_draft_change_aborts_before_dolphin_call(monkeypatch):
    db, workflow_id, old_active_id, _node_id = _fixture()

    class Engine:
        def publish_definition(self, *_args, **_kwargs):
            raise AssertionError("changed draft must not reach Dolphin")

    publish = _patch_publish(monkeypatch, Engine())
    original_commit = db.commit
    commit_count = 0

    def commit_with_concurrent_edit():
        nonlocal commit_count
        original_commit()
        commit_count += 1
        if commit_count == 1:
            with sessionmaker(bind=db.get_bind())() as other:
                changed = other.query(Workflow).filter(Workflow.id == workflow_id).one()
                changed.dag_config = {"nodes": [], "edges": [], "draft": "changed"}
                other.commit()

    monkeypatch.setattr(db, "commit", commit_with_concurrent_edit)
    workflow = db.query(Workflow).filter(Workflow.id == workflow_id).one()
    with pytest.raises(RuntimeError, match="DAG 已被修改"):
        publish.publish_workflow_to_ds(db, workflow)

    db.expire_all()
    assert db.query(Workflow).filter(Workflow.id == workflow_id).one().active_version_id == old_active_id
    assert (
        db.query(JobVersion)
        .filter(JobVersion.workflow_id == workflow_id, JobVersion.status == "failed")
        .one()
        .build_finished_at
        is not None
    )


def test_workflow_version_number_is_unique_under_concurrent_allocation():
    db, workflow_id, _old_active_id, _node_id = _fixture()
    db.add(JobVersion(workflow_id=workflow_id, version_no=2, status="building"))
    db.commit()
    db.add(JobVersion(workflow_id=workflow_id, version_no=2, status="failed"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_job_version_migration_does_not_create_or_drop_legacy_artifact_table():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE dw_job_versions ("
                "id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL, "
                "version_no INTEGER NOT NULL, status VARCHAR(32))"
            )
        )
        connection.execute(
            text("CREATE TABLE dw_python_node_artifacts (id INTEGER PRIMARY KEY)")
        )

    migrate_job_version_publishing(engine)
    migration_tables = set(inspect(engine).get_table_names())
    assert "dw_python_node_artifacts" in migration_tables

    clean_engine = create_engine("sqlite://")
    with clean_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE dw_job_versions ("
                "id INTEGER PRIMARY KEY, workflow_id INTEGER NOT NULL, "
                "version_no INTEGER NOT NULL, status VARCHAR(32))"
            )
        )
    migrate_job_version_publishing(clean_engine)
    assert "dw_python_node_artifacts" not in inspect(clean_engine).get_table_names()

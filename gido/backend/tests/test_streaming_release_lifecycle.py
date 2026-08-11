# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.api.streaming import (
    StreamingJob,
    StreamingJobHistory,
    StreamingJobRelease,
    StreamingDeployBody,
    StreamingRestartBody,
    StreamingRestorePoint,
    StreamingStopBody,
    JobUpdate,
    approve_streaming_job_release,
    create_streaming_job_release,
    create_streaming_operation,
    deploy_streaming_job_release,
    _runtime_job_from_release,
    _streaming_restore_point_public_dict,
    restart_streaming_job,
    stop_streaming_job_with_savepoint,
    update_job,
)
from app.core.database import Base
from app.api.stream_pipeline import StreamConnectionProfile
from app.models.workspace import PublishApproval, WorkspaceVariable
from app.services.stream_pipeline_runtime import (
    resolve_pipeline_runtime,
    resolve_pipeline_sql_for_runtime,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _job(db):
    job = StreamingJob(
        workspace_id=1,
        name="release-test",
        job_type="SQL",
        script_content="INSERT INTO sink SELECT * FROM source",
        parallelism=2,
        flink_sql_submit_mode="session",
        flink_jar_submit_mode="session",
        status="draft",
        lifecycle_state="draft",
        created_by=7,
        owner_id=7,
        is_locked=False,
    )
    db.add(job)
    db.flush()
    return job


def _pipeline_profiles(db, *, with_kafka_secret: bool = False):
    rows = [
        StreamConnectionProfile(
            id=1,
            workspace_id=1,
            name="kafka",
            connector_type="kafka",
            options={"bootstrap.servers": "kafka:9092"},
            secret_refs=(
                {"properties.sasl.jaas.config": "kafka_jaas"}
                if with_kafka_secret
                else {}
            ),
        ),
        StreamConnectionProfile(
            id=2,
            workspace_id=1,
            name="paimon",
            connector_type="paimon",
            options={
                "warehouse": "s3://warehouse/paimon",
                "allowed.namespaces": "ods",
            },
            secret_refs={},
        ),
    ]
    if with_kafka_secret:
        rows.append(
            WorkspaceVariable(
                workspace_id=1,
                var_key="kafka_jaas",
                var_value='login required username="u" password="p";',
                is_secret=True,
                scope="stream",
            )
        )
    db.add_all(rows)
    db.flush()


def test_release_submission_snapshots_without_mutating_draft():
    db = _session()
    job = _job(db)

    release = create_streaming_job_release(
        db,
        job,
        7,
        script_content="INSERT INTO sink SELECT id FROM source",
        release_note="candidate",
    )
    db.commit()

    assert release.version == 1
    assert release.approval_status == "pending"
    assert release.script_content == "INSERT INTO sink SELECT id FROM source"
    assert job.script_content == "INSERT INTO sink SELECT * FROM source"
    assert job.flink_job_id is None
    assert job.status == "draft"
    assert job.lifecycle_state == "pending_approval"


def test_release_snapshot_is_immutable_but_approval_is_allowed():
    db = _session()
    job = _job(db)
    release = create_streaming_job_release(db, job, 7)
    db.commit()

    release.script_content = "SELECT changed"
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()

    release = db.query(StreamingJobRelease).filter_by(id=release.id).one()
    job = db.query(StreamingJob).filter_by(id=job.id).one()
    approve_streaming_job_release(db, job, release, 99, comment="approved")
    db.commit()
    assert release.approval_status == "approved"
    assert job.current_approved_release_id == release.id
    assert job.lifecycle_state == "approved"


def test_pipeline_release_recompiles_and_snapshots_deterministic_artifact():
    db = _session()
    job = _job(db)
    _pipeline_profiles(db)
    job.definition_kind = "pipeline"
    job.pipeline_spec = {
        "spec_version": "1.0",
        "kind": "kafka_to_paimon",
        "mode": "append",
        "source": {
            "connection_profile_id": 1,
            "topic": "events",
            "consumer_group": "gido-events",
            "format": "json",
        },
        "sink": {
            "connection_profile_id": 2,
            "database": "ods",
            "table": "events",
        },
        "schema": [{"name": "id", "data_type": "BIGINT", "nullable": False}],
    }
    job.generated_artifact = {"stale": True}
    job.spec_hash = "stale"

    release = create_streaming_job_release(db, job, 7)
    db.commit()

    assert release.definition_kind == "pipeline"
    assert release.spec_hash != "stale"
    assert release.generated_artifact["spec_hash"] == release.spec_hash
    assert release.script_content == release.generated_artifact["sql"]
    assert "stale" not in release.generated_artifact

    release.pipeline_spec = {"changed": True}
    with pytest.raises(ValueError, match="immutable"):
        db.commit()


def test_pipeline_runtime_uses_release_spec_and_resolves_secret_refs():
    db = _session()
    job = _job(db)
    _pipeline_profiles(db, with_kafka_secret=True)
    job.definition_kind = "pipeline"
    job.pipeline_spec = {
        "spec_version": "1.0",
        "kind": "kafka_to_paimon",
        "mode": "append",
        "source": {
            "connection_profile_id": 1,
            "topic": "events",
            "consumer_group": "gido-events",
            "format": "json",
        },
        "sink": {
            "connection_profile_id": 2,
            "database": "ods",
            "table": "events",
        },
        "schema": [{"name": "id", "data_type": "BIGINT", "nullable": False}],
    }
    release = create_streaming_job_release(db, job, 7)
    db.commit()
    job.pipeline_spec = {**job.pipeline_spec, "source": {**job.pipeline_spec["source"], "topic": "draft-events"}}

    runtime = _runtime_job_from_release(job, release)
    assert runtime.pipeline_spec["source"]["topic"] == "events"
    resolved = resolve_pipeline_runtime(
        db, workspace_id=1, spec=runtime.pipeline_spec
    )
    sql = resolved.sql
    assert "'properties.bootstrap.servers' = 'kafka:9092'" in sql
    assert "'warehouse' = 's3://warehouse/paimon'" in sql
    assert 'username="u"' not in sql
    assert "${env:GIDO_PIPELINE_SECRET_" in sql
    assert list(resolved.secret_env.values()) == [
        'login required username="u" password="p";'
    ]

    db.query(StreamConnectionProfile).filter_by(id=2).one().options = {
        "warehouse": "s3://warehouse/paimon",
        "allowed.namespaces": "dwd",
    }
    db.flush()
    with pytest.raises(ValueError, match="outside the connection profile whitelist"):
        resolve_pipeline_sql_for_runtime(
            db, workspace_id=1, spec=runtime.pipeline_spec
        )


def test_operation_idempotency_returns_existing_record():
    db = _session()
    job = _job(db)
    first = create_streaming_operation(
        db, job, "deploy", 7, idempotency_key="deploy-job-1-release-1"
    )
    second = create_streaming_operation(
        db, job, "deploy", 7, idempotency_key="deploy-job-1-release-1"
    )
    assert second.id == first.id
    assert db.query(type(first)).count() == 1


def test_operation_idempotency_rejects_different_request():
    db = _session()
    job = _job(db)
    create_streaming_operation(
        db,
        job,
        "deploy",
        7,
        idempotency_key="same-key",
        request_payload={"release_id": 1},
    )
    with pytest.raises(HTTPException, match="不同的生命周期请求"):
        create_streaming_operation(
            db,
            job,
            "stop",
            7,
            idempotency_key="same-key",
            request_payload={"mode": "savepoint"},
        )


def test_runtime_override_cannot_replace_approved_dependencies():
    from app.api import streaming as streaming_api

    with pytest.raises(HTTPException, match="已批准发布内容"):
        streaming_api._merge_release_runtime_properties(
            '{"pipeline.jars":"s3://approved.jar"}',
            '{"pipeline.jars":"s3://other.jar"}',
        )
    merged = streaming_api._merge_release_runtime_properties(
        '{"pipeline.jars":"s3://approved.jar","execution.checkpointing.interval":"60s"}',
        '{"operator_resources":{"taskManager":{"memory":"4096m","replicas":2}}}',
    )
    assert '"pipeline.jars": "s3://approved.jar"' in merged
    assert '"execution.checkpointing.interval": "60s"' in merged
    assert '"memory": "4096m"' in merged
    with pytest.raises(HTTPException, match="不可覆盖字段"):
        streaming_api._merge_release_runtime_properties(
            "{}",
            '{"operator_resources":{"taskManager":{"env":"unsafe"}}}',
        )

    merged = streaming_api._merge_release_runtime_properties(
        '{"pipeline.jars":"s3://approved.jar"}',
        '{"operator_resources":{"taskSlots":4}}',
    )
    assert '"pipeline.jars": "s3://approved.jar"' in (merged or "")


def test_definition_kind_only_cannot_bypass_pipeline_preflight(monkeypatch):
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db)
    db.commit()
    monkeypatch.setattr(
        streaming_api,
        "require_streaming_job",
        lambda *args, **kwargs: job,
    )
    with pytest.raises(HTTPException, match="必须提供 pipeline_spec"):
        update_job(
            job.id,
            JobUpdate(definition_kind="pipeline"),
            db=db,
            current_user=SimpleNamespace(id=7),
            create_history=False,
        )


def _approved_operator_release(db, job):
    job.flink_sql_submit_mode = "flink_operator"
    release = create_streaming_job_release(db, job, 7)
    approve_streaming_job_release(db, job, release, 99)
    db.commit()
    return release


def test_deploy_uses_immutable_release_without_overwriting_draft(monkeypatch):
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    job.script_content = "SELECT draft_changed_after_approval"
    db.commit()

    def _deploy(_db, source_job, source_release, user, **kwargs):
        runtime_job = streaming_api._runtime_job_from_release(
            source_job,
            source_release,
            parallelism=kwargs.get("parallelism"),
            streaming_properties=kwargs.get("streaming_properties"),
        )
        runtime_job.flink_job_id = "jid-release"
        runtime_job.flink_operator_deployment_name = "gido-sql-1-1"
        runtime_job.status = "running"
        return runtime_job, {"flink_job_id": "jid-release"}

    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(streaming_api, "_execute_approved_release_deployment", _deploy)

    out = deploy_streaming_job_release(
        job.id,
        StreamingDeployBody(release_id=release.id, parallelism=4),
        db=db,
        current_user=SimpleNamespace(id=7),
    )

    assert out["release_id"] == release.id
    assert job.script_content == "SELECT draft_changed_after_approval"
    assert job.current_running_release_id == release.id
    assert job.flink_job_id == "jid-release"
    assert job.lifecycle_state == "RUNNING"
    assert db.query(StreamingJobHistory).filter_by(job_id=job.id).count() == 0


def test_stop_persists_new_savepoint_and_suspends(monkeypatch):
    from app.api import streaming as streaming_api
    from app.services import flink_operator_submit

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    job.current_running_release_id = release.id
    job.flink_operator_deployment_name = "gido-sql-1-1"
    job.flink_job_id = "jid-running"
    job.status = "running"
    job.lifecycle_state = "RUNNING"
    db.commit()

    cr = {
        "spec": {
            "flinkVersion": "v2_0",
            "flinkConfiguration": {"state.savepoints.dir": "s3://state/savepoints"},
            "job": {"parallelism": 2},
        }
    }
    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(flink_operator_submit, "_operator_namespace", lambda: "flink")
    monkeypatch.setattr(flink_operator_submit, "read_flink_deployment", lambda *a, **k: cr)
    monkeypatch.setattr(
        flink_operator_submit,
        "extract_savepoint_status_from_cr",
        lambda value: ("COMPLETED", "s3://state/savepoints/old", None),
    )
    monkeypatch.setattr(flink_operator_submit, "suspend_flink_deployment", lambda *a, **k: {})
    monkeypatch.setattr(
        flink_operator_submit,
        "ensure_flink_deployment_savepoint_dirs",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "wait_for_completed_savepoint",
        lambda *a, **k: "s3://state/savepoints/new",
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "wait_for_flink_deployment_suspended",
        lambda *a, **k: cr,
    )

    out = stop_streaming_job_with_savepoint(
        job.id,
        StreamingStopBody(timeout_seconds=10, wait=True),
        db=db,
        current_user=SimpleNamespace(id=7),
    )

    assert out["restore_point"]["path"] == "s3://state/savepoints/new"
    assert job.status == "cancelled"
    assert job.lifecycle_state == "SUSPENDED"


def test_stop_timeout_keeps_job_running_and_audits_failure(monkeypatch):
    from app.api import streaming as streaming_api
    from app.services import flink_operator_submit

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    job.current_running_release_id = release.id
    job.flink_operator_deployment_name = "gido-sql-1-1"
    job.flink_job_id = "jid-running"
    job.status = "running"
    job.lifecycle_state = "RUNNING"
    db.commit()

    cr = {
        "spec": {
            "flinkConfiguration": {"state.savepoints.dir": "s3://state/savepoints"},
            "job": {"parallelism": 2},
        }
    }
    resumed = []
    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(flink_operator_submit, "_operator_namespace", lambda: "flink")
    monkeypatch.setattr(flink_operator_submit, "read_flink_deployment", lambda *a, **k: cr)
    monkeypatch.setattr(
        flink_operator_submit,
        "extract_savepoint_status_from_cr",
        lambda value: (None, None, None),
    )
    monkeypatch.setattr(flink_operator_submit, "suspend_flink_deployment", lambda *a, **k: {})
    monkeypatch.setattr(
        flink_operator_submit,
        "ensure_flink_deployment_savepoint_dirs",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "wait_for_completed_savepoint",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("savepoint timeout")),
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "resume_flink_deployment",
        lambda *a, **k: (
            resumed.append(True),
            cr["spec"]["job"].__setitem__("state", "running"),
        ),
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "wait_for_flink_deployment_running",
        lambda *a, **k: cr,
    )

    with pytest.raises(HTTPException, match="作业仍在运行"):
        stop_streaming_job_with_savepoint(
            job.id,
            StreamingStopBody(timeout_seconds=10, wait=True),
            db=db,
            current_user=SimpleNamespace(id=7),
        )

    assert resumed == [True]
    assert job.status == "running"
    assert job.lifecycle_state == "RUNNING"


def test_stop_failure_unknown_cluster_stays_running(monkeypatch):
    """resume 结果未知时也不进入 STOP_FAILED 悬空态。"""
    from app.api import streaming as streaming_api
    from app.services import flink_operator_submit

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    job.current_running_release_id = release.id
    job.flink_operator_deployment_name = "gido-sql-1-1"
    job.flink_job_id = "jid-running"
    job.status = "running"
    job.lifecycle_state = "RUNNING"
    db.commit()

    cr = {
        "spec": {
            "flinkConfiguration": {"state.savepoints.dir": "s3://state/savepoints"},
            "job": {"parallelism": 2},
        }
    }
    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(flink_operator_submit, "_operator_namespace", lambda: "flink")
    monkeypatch.setattr(flink_operator_submit, "read_flink_deployment", lambda *a, **k: cr)
    monkeypatch.setattr(
        flink_operator_submit,
        "extract_savepoint_status_from_cr",
        lambda value: (None, None, None),
    )
    monkeypatch.setattr(flink_operator_submit, "suspend_flink_deployment", lambda *a, **k: {})
    monkeypatch.setattr(
        flink_operator_submit,
        "ensure_flink_deployment_savepoint_dirs",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "wait_for_completed_savepoint",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("savepoint timeout")),
    )
    monkeypatch.setattr(
        flink_operator_submit,
        "resume_flink_deployment",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("resume failed")),
    )

    with pytest.raises(HTTPException, match="仍运行中"):
        stop_streaming_job_with_savepoint(
            job.id,
            StreamingStopBody(timeout_seconds=10, wait=True),
            db=db,
            current_user=SimpleNamespace(id=7),
        )

    assert job.status == "running"
    assert job.lifecycle_state == "RUNNING"


def test_restart_from_suspended_uses_restore_path_not_bare_resume(monkeypatch):
    """干净挂起（spec=suspended）+ 恢复点：走 savepointRedeploy，不能只 resume。"""
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    job.current_running_release_id = release.id
    job.flink_operator_deployment_name = "gido-sql-1-1"
    job.status = "cancelled"
    job.lifecycle_state = "SUSPENDED"
    restore = StreamingRestorePoint(
        job_id=job.id,
        release_id=release.id,
        point_type="savepoint",
        status="completed",
        path="s3://state/savepoints/chosen",
        metadata_json='{"flink_version":"v2_0"}',
        created_by=7,
        completed_at=datetime.utcnow(),
    )
    db.add(restore)
    db.commit()

    redeployed = []
    resumed = []
    replaced = []

    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(
        streaming_api,
        "_execute_approved_release_deployment",
        lambda *a, **k: replaced.append(dict(k)) or (_ for _ in ()).throw(
            AssertionError("clean suspend should patch, not replace")
        ),
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.resume_flink_deployment",
        lambda *a, **k: resumed.append(dict(k)),
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.resume_flink_deployment_from_savepoint",
        lambda *a, **k: redeployed.append(dict(k)) or {},
    )
    suspended_cr = {
        "spec": {"job": {"state": "suspended"}},
        "status": {
            "lifecycleState": "SUSPENDED",
            "jobStatus": {"state": "FINISHED"},
            "taskManager": {"replicas": 0},
        },
    }
    monkeypatch.setattr(
        "app.services.flink_operator_submit.read_flink_deployment",
        lambda *a, **k: suspended_cr,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_for_flink_deployment_running",
        lambda *a, **k: {
            "spec": {"job": {"state": "running"}},
            "status": {
                "lifecycleState": "STABLE",
                "jobStatus": {"state": "RUNNING", "jobId": "jid-restored"},
                "taskManager": {"replicas": 1},
            },
        },
    )
    monkeypatch.setattr(streaming_api.settings, "FLINK_OPERATOR_FLINK_VERSION", "v2_0")

    out = restart_streaming_job(
        job.id,
        StreamingRestartBody(release_id=release.id, restore_mode="latest"),
        db=db,
        current_user=SimpleNamespace(id=7),
    )

    assert resumed == []
    assert replaced == []
    assert out.get("restart_action") == "savepoint_redeploy"
    assert redeployed and redeployed[0]["savepoint_path"] == "s3://state/savepoints/chosen"
    assert job.lifecycle_state == "RUNNING"


def test_restart_stuck_running_spec_with_suspended_status_replaces(monkeypatch):
    """spec=running 但 lifecycle 仍 SUSPENDED/FINISHED：回收 CR 后带 path 重建。"""
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    job.current_running_release_id = release.id
    job.flink_operator_deployment_name = "gido-sql-1-1"
    job.status = "cancelled"
    job.lifecycle_state = "SUSPENDED"
    restore = StreamingRestorePoint(
        job_id=job.id,
        release_id=release.id,
        point_type="savepoint",
        status="completed",
        path="s3://state/savepoints/stuck-sp",
        metadata_json='{"flink_version":"v2_0"}',
        created_by=7,
        completed_at=datetime.utcnow(),
    )
    db.add(restore)
    db.commit()

    gone = []
    captured = {}
    redeployed = []

    def _deploy(_db, source_job, source_release, user, **kwargs):
        captured.update(kwargs)
        runtime_job = streaming_api._runtime_job_from_release(
            source_job,
            source_release,
            parallelism=kwargs.get("parallelism"),
            streaming_properties=kwargs.get("streaming_properties"),
        )
        runtime_job.flink_job_id = "jid-new"
        runtime_job.flink_operator_deployment_name = "gido-sql-1-1"
        runtime_job.status = "running"
        return runtime_job, {"flink_job_id": "jid-new"}

    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(streaming_api, "_execute_approved_release_deployment", _deploy)
    monkeypatch.setattr(
        "app.services.flink_operator_submit.ensure_flink_deployment_gone",
        lambda *a, **k: gone.append(True) or "gone",
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.resume_flink_deployment_from_savepoint",
        lambda *a, **k: redeployed.append(True),
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.read_flink_deployment",
        lambda *a, **k: {
            "spec": {"job": {"state": "running", "initialSavepointPath": "s3://old"}},
            "status": {
                "lifecycleState": "SUSPENDED",
                "jobStatus": {"state": "FINISHED"},
                "taskManager": {"replicas": 0},
            },
        },
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_for_flink_deployment_running",
        lambda *a, **k: {
            "spec": {"job": {"state": "running"}},
            "status": {
                "lifecycleState": "STABLE",
                "jobStatus": {"state": "RUNNING", "jobId": "jid-new"},
                "taskManager": {"replicas": 1},
            },
        },
    )
    monkeypatch.setattr(streaming_api.settings, "FLINK_OPERATOR_FLINK_VERSION", "v2_0")

    out = restart_streaming_job(
        job.id,
        StreamingRestartBody(release_id=release.id, restore_mode="latest"),
        db=db,
        current_user=SimpleNamespace(id=7),
    )

    assert gone == [True]
    assert redeployed == []
    assert captured["restore_path"] == "s3://state/savepoints/stuck-sp"
    assert out.get("restart_action") == "replace_deployment"
    assert job.lifecycle_state == "RUNNING"


def test_restart_uses_selected_completed_restore_point(monkeypatch):
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db)
    release = _approved_operator_release(db, job)
    restore = StreamingRestorePoint(
        job_id=job.id,
        release_id=release.id,
        point_type="savepoint",
        status="completed",
        path="s3://state/savepoints/chosen",
        metadata_json='{"flink_version":"v2_0"}',
        created_by=7,
    )
    db.add(restore)
    db.commit()

    captured = {}

    def _restart(_db, source_job, source_release, user, **kwargs):
        captured.update(kwargs)
        runtime_job = streaming_api._runtime_job_from_release(
            source_job,
            source_release,
            parallelism=kwargs.get("parallelism"),
            streaming_properties=kwargs.get("streaming_properties"),
        )
        runtime_job.flink_job_id = "jid-restored"
        runtime_job.flink_operator_deployment_name = "gido-sql-1-1"
        runtime_job.status = "running"
        return runtime_job, {"flink_job_id": "jid-restored"}

    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(streaming_api, "_execute_approved_release_deployment", _restart)
    monkeypatch.setattr(
        "app.services.flink_operator_submit.read_flink_deployment",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("missing")),
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_for_flink_deployment_running",
        lambda *a, **k: {
            "spec": {"job": {"state": "running"}},
            "status": {
                "jobStatus": {"state": "RUNNING", "jobId": "jid-restored"},
                "taskManager": {"replicas": 1},
            },
        },
    )
    monkeypatch.setattr(streaming_api.settings, "FLINK_OPERATOR_FLINK_VERSION", "v2_0")

    out = restart_streaming_job(
        job.id,
        StreamingRestartBody(
            release_id=release.id,
            restore_mode="specific",
            restore_point_id=restore.id,
        ),
        db=db,
        current_user=SimpleNamespace(id=7),
    )

    assert captured["restore_path"] == "s3://state/savepoints/chosen"
    assert out["restore_point_id"] == restore.id
    assert job.lifecycle_state == "RUNNING"


def test_publish_approval_approves_exact_bound_release(monkeypatch):
    from app.services import publish_approval

    db = _session()
    job = _job(db)
    first = create_streaming_job_release(db, job, 7, release_note="reviewed")
    second = create_streaming_job_release(db, job, 7, release_note="newer")
    approval = PublishApproval(
        workspace_id=job.workspace_id,
        resource_type="stream_job",
        resource_id=job.id,
        release_id=first.id,
        resource_name=job.name,
        action="submit_job",
        status="pending",
        submitted_by=7,
    )
    db.add(approval)
    db.commit()
    monkeypatch.setattr(
        publish_approval,
        "assert_can_publish_production",
        lambda *a, **k: None,
    )

    publish_approval.approve_publish_approval(
        db,
        SimpleNamespace(id=99),
        approval.id,
        review_note="approved exact snapshot",
    )

    assert first.approval_status == "approved"
    assert second.approval_status == "pending"
    assert approval.status == "approved"
    assert first.approval_comment == "approved exact snapshot"


def test_publish_approval_submission_binds_requested_release(monkeypatch):
    from app.services import publish_approval

    db = _session()
    job = _job(db)
    first = create_streaming_job_release(db, job, 7, release_note="selected")
    create_streaming_job_release(db, job, 7, release_note="newer")
    db.commit()
    monkeypatch.setattr(
        publish_approval,
        "_assert_approval_workspace_access",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        publish_approval,
        "workspace_data_full_control",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        publish_approval,
        "_resolve_resource",
        lambda *a, **k: (job.name, job),
    )

    approval = publish_approval.submit_publish_approval(
        db,
        SimpleNamespace(id=7),
        job.workspace_id,
        "stream_job",
        job.id,
        "submit_job",
        "review selected",
        first.id,
    )

    assert approval.release_id == first.id


def test_restore_point_public_dict_includes_duration_for_failed():
    started = datetime(2026, 8, 11, 3, 0, 0)
    ended = started + timedelta(seconds=125)
    row = StreamingRestorePoint(
        id=9,
        job_id=1,
        point_type="savepoint",
        status="failed",
        error_message="Timed out waiting for savepoint",
        created_at=started,
        completed_at=ended,
    )
    out = _streaming_restore_point_public_dict(row)
    assert out["duration_seconds"] == 125
    assert out["status"] == "failed"
    assert out["completed_at"] == ended

    pending = StreamingRestorePoint(
        id=10,
        job_id=1,
        point_type="savepoint",
        status="pending",
        created_at=started,
        completed_at=None,
    )
    assert _streaming_restore_point_public_dict(pending)["duration_seconds"] is None

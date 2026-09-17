# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.streaming import (
    StreamingDeployBody,
    StreamingJob,
    StreamingJobRelease,
    StreamingRestartBody,
    SubmitJobBody,
    create_streaming_job_release,
    deploy_streaming_job_release,
    restart_streaming_job,
    submit_job,
)
from app.core.database import Base
from app.services.stream_sql_risk import (
    assess_stream_sql,
    assert_risk_confirmation,
    combine_operation_risks,
)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _job(db, sql: str) -> StreamingJob:
    job = StreamingJob(
        workspace_id=1,
        name="risk-test",
        job_type="SQL",
        script_content=sql,
        parallelism=1,
        flink_sql_submit_mode="flink_operator",
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


def test_comments_and_quoted_keywords_are_not_executable():
    assessment = assess_stream_sql(
        """
        -- DROP TABLE hidden;
        # TRUNCATE TABLE hidden;
        /* DELETE FROM hidden; */
        SELECT 'DROP TABLE x; DELETE FROM y', "TRUNCATE z", `DROP`;
        """
    )
    assert assessment["level"] == "low"
    assert assessment["risks"] == []


def test_real_drop_and_statement_indexes_are_detected():
    assessment = assess_stream_sql("SELECT 1; DROP TABLE IF EXISTS cat.db.orders;")
    assert assessment["level"] == "critical"
    assert assessment["requires_confirmation"] is True
    assert assessment["risks"][0]["code"] == "DROP_TABLE"
    assert assessment["risks"][0]["statement_index"] == 1
    assert assessment["risks"][0]["object_name"] == "cat.db.orders"


def test_paimon_catalog_and_current_catalog_are_recognized():
    assessment = assess_stream_sql(
        """
        CREATE CATALOG lake WITH ('type' = 'paimon');
        USE CATALOG lake;
        DELETE FROM ods.orders;
        """
    )
    assert assessment["risks"][0]["code"] == "DELETE_FROM"
    assert assessment["risks"][0]["is_paimon"] is True


@pytest.mark.parametrize(
    ("sql", "code", "level"),
    [
        ("DROP TEMPORARY TABLE t", "DROP_TABLE", "high"),
        ("DROP CATALOG c", "DROP_CATALOG", "critical"),
        ("DROP DATABASE d", "DROP_DATABASE", "critical"),
        ("DROP VIEW v", "DROP_VIEW", "high"),
        ("DROP MATERIALIZED VIEW mv", "DROP_MATERIALIZED_VIEW", "high"),
        ("TRUNCATE TABLE t", "TRUNCATE", "critical"),
        ("ALTER TABLE t DROP COLUMN old_value", "ALTER_TABLE_DROP", "critical"),
        ("DELETE FROM t", "DELETE_FROM", "high"),
        ("INSERT OVERWRITE TABLE t SELECT 1", "INSERT_OVERWRITE", "high"),
        ("CREATE OR REPLACE TABLE t (id INT)", "CREATE_OR_REPLACE", "high"),
    ],
)
def test_all_contract_risk_statements(sql, code, level):
    assessment = assess_stream_sql(sql)
    assert assessment["risks"][0]["code"] == code
    assert assessment["risks"][0]["level"] == level


def test_paimon_table_connector_is_tracked_across_statements():
    assessment = assess_stream_sql(
        """
        CREATE TABLE lake.ods.orders (id BIGINT)
        WITH ('connector' = 'paimon');
        TRUNCATE TABLE lake.ods.orders;
        """
    )
    assert assessment["risks"][0]["is_paimon"] is True


def test_confirmation_requires_current_hash_and_all_codes():
    assessment = combine_operation_risks(
        assess_stream_sql("DROP TABLE t"),
        action="restart",
        restore_mode="stateless",
    )
    with pytest.raises(HTTPException) as exc:
        assert_risk_confirmation(assessment, "stale", ["DROP_TABLE"])
    assert exc.value.status_code == 409
    assert exc.value.detail == assessment

    assert_risk_confirmation(
        assessment,
        assessment["assessment_hash"],
        ["DROP_TABLE", "STATELESS_RESTART"],
    )


def test_duplicate_dangerous_statements_require_individual_confirmation():
    assessment = assess_stream_sql("DROP TABLE first_sink; DROP TABLE second_sink")
    required = [risk["confirmation_code"] for risk in assessment["risks"]]
    assert required == ["DROP_TABLE:1", "DROP_TABLE:2"]
    with pytest.raises(HTTPException):
        assert_risk_confirmation(
            assessment,
            assessment["assessment_hash"],
            ["DROP_TABLE:1"],
        )
    assert_risk_confirmation(assessment, assessment["assessment_hash"], required)


def test_create_release_rejects_before_insert_then_freezes_assessment():
    db = _session()
    job = _job(db, "DROP TABLE sink")
    assessment = assess_stream_sql(job.script_content)
    with pytest.raises(HTTPException) as exc:
        create_streaming_job_release(db, job, 7)
    assert exc.value.status_code == 409
    assert db.query(StreamingJobRelease).count() == 0

    release = create_streaming_job_release(
        db,
        job,
        7,
        risk_assessment_hash=assessment["assessment_hash"],
        confirmed_risk_codes=["DROP_TABLE"],
    )
    assert release.risk_assessment == assessment
    assert release.risk_assessment_hash == assessment["assessment_hash"]


def test_deploy_rejects_risk_before_operation_or_external_call(monkeypatch):
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db, "DROP TABLE sink")
    release_assessment = assess_stream_sql(job.script_content)
    release = create_streaming_job_release(
        db,
        job,
        7,
        risk_assessment_hash=release_assessment["assessment_hash"],
        confirmed_risk_codes=["DROP_TABLE"],
    )
    release.approval_status = "approved"
    db.commit()
    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    called = []
    monkeypatch.setattr(
        streaming_api,
        "_execute_approved_release_deployment",
        lambda *a, **k: called.append(True),
    )

    with pytest.raises(HTTPException) as exc:
        deploy_streaming_job_release(
            job.id,
            StreamingDeployBody(release_id=release.id),
            db=db,
            current_user=SimpleNamespace(id=7),
        )
    assert exc.value.status_code == 409
    assert called == []
    assert db.query(streaming_api.StreamingOperation).count() == 0


def test_stateless_restart_rejects_before_operation(monkeypatch):
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db, "SELECT 1")
    release = create_streaming_job_release(db, job, 7)
    release.approval_status = "approved"
    db.commit()
    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)

    with pytest.raises(HTTPException) as exc:
        restart_streaming_job(
            job.id,
            StreamingRestartBody(release_id=release.id, restore_mode="stateless"),
            db=db,
            current_user=SimpleNamespace(id=7),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["risks"][0]["code"] == "STATELESS_RESTART"
    assert db.query(streaming_api.StreamingOperation).count() == 0


def test_deprecated_direct_submit_cannot_bypass_risk_confirmation(monkeypatch):
    from app.api import streaming as streaming_api

    db = _session()
    job = _job(db, "SELECT 1")
    monkeypatch.setattr(streaming_api, "require_streaming_job", lambda *a, **k: job)
    monkeypatch.setattr(
        streaming_api, "assert_can_publish_production", lambda *a, **k: None
    )
    called = []
    monkeypatch.setattr(
        streaming_api,
        "execute_streaming_job_submit",
        lambda *a, **k: called.append(True),
    )

    with pytest.raises(HTTPException) as exc:
        submit_job(
            job.id,
            SubmitJobBody(script_content="DROP TABLE sink"),
            db=db,
            current_user=SimpleNamespace(id=7),
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["risks"][0]["code"] == "DROP_TABLE"
    assert called == []

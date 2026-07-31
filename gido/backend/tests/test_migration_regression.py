# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""启动迁移函数的幂等回归：不连真实 DB，仅用内存 SQLite + create_all。"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from sqlalchemy import create_engine, inspect


def _fresh_engine():
    return create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


def _load_models():
    import app.models.workspace  # noqa: F401
    import app.models.rbac_models  # noqa: F401


def test_migrate_task_nodes_owner_lock_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import migrate_dw_task_nodes_owner_lock, migrate_dw_task_nodes_edit_lock

    _load_models()
    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_task_nodes_owner_lock(eng)
    migrate_dw_task_nodes_owner_lock(eng)
    migrate_dw_task_nodes_edit_lock(eng)
    migrate_dw_task_nodes_edit_lock(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("dw_task_nodes")}
    assert "owner_id" in cols
    assert "is_locked" in cols
    assert "edit_lock_user_id" in cols
    assert "edit_lock_at" in cols


def test_migrate_workflow_audit_columns_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import migrate_dw_workflow_updated_by, migrate_dw_workflow_instance_submitted_by

    _load_models()
    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_workflow_updated_by(eng)
    migrate_dw_workflow_updated_by(eng)
    migrate_dw_workflow_instance_submitted_by(eng)
    migrate_dw_workflow_instance_submitted_by(eng)
    assert "updated_by" in {c["name"] for c in inspect(eng).get_columns("dw_workflows")}
    assert "submitted_by" in {c["name"] for c in inspect(eng).get_columns("dw_workflow_instances")}


def test_migrate_quality_dolphin_refs_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import migrate_dw_quality_dolphin_refs

    _load_models()
    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_quality_dolphin_refs(eng)
    migrate_dw_quality_dolphin_refs(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("dw_quality_rules")}
    assert "dolphin_refs" in cols


def test_migrate_streaming_jobs_owner_lock_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import migrate_dw_streaming_jobs

    _load_models()
    import app.api.streaming  # noqa: F401 — 注册 dw_streaming_jobs

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_streaming_jobs(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_jobs")}
    assert "owner_id" in cols
    assert "is_locked" in cols


def test_migrate_streaming_release_lifecycle_idempotent_on_legacy_table():
    from sqlalchemy import text
    from app.services.rbac_seed import migrate_dw_streaming_release_lifecycle

    eng = _fresh_engine()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dw_streaming_jobs (
                  id INTEGER PRIMARY KEY,
                  workspace_id INTEGER,
                  name VARCHAR(128),
                  job_type VARCHAR(16),
                  status VARCHAR(32)
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO dw_streaming_jobs "
                "(id, workspace_id, name, job_type, status) "
                "VALUES (1, 1, 'legacy-job', 'SQL', 'running')"
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE dw_publish_approvals (
                  id INTEGER PRIMARY KEY,
                  workspace_id INTEGER,
                  resource_id INTEGER,
                  status VARCHAR(32)
                )
                """
            )
        )

    migrate_dw_streaming_release_lifecycle(eng)
    migrate_dw_streaming_release_lifecycle(eng)

    inspector = inspect(eng)
    job_cols = {c["name"] for c in inspector.get_columns("dw_streaming_jobs")}
    assert {
        "current_approved_release_id",
        "current_running_release_id",
        "lifecycle_state",
    }.issubset(job_cols)
    assert inspector.has_table("dw_streaming_job_releases")
    assert inspector.has_table("dw_streaming_restore_points")
    assert inspector.has_table("dw_streaming_operations")
    assert any(
        fk["referred_table"] == "dw_streaming_jobs"
        and fk["constrained_columns"] == ["job_id"]
        for fk in inspector.get_foreign_keys("dw_streaming_job_releases")
    )
    assert "release_id" in {
        c["name"] for c in inspector.get_columns("dw_publish_approvals")
    }
    with eng.connect() as conn:
        assert conn.execute(
            text("SELECT lifecycle_state FROM dw_streaming_jobs WHERE id = 1")
        ).scalar_one() == "running"


def test_migrate_stream_pipeline_foundation_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import (
        migrate_dw_stream_pipeline,
        migrate_dw_streaming_release_lifecycle,
    )

    _load_models()
    import app.api.streaming  # noqa: F401
    import app.api.stream_pipeline  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_release_lifecycle(eng)
    migrate_dw_stream_pipeline(eng)
    migrate_dw_stream_pipeline(eng)

    inspector = inspect(eng)
    expected_columns = {
        "definition_kind",
        "pipeline_spec",
        "compiler_version",
        "generated_artifact",
        "spec_hash",
    }
    assert expected_columns.issubset(
        {column["name"] for column in inspector.get_columns("dw_streaming_jobs")}
    )
    assert expected_columns.issubset(
        {column["name"] for column in inspector.get_columns("dw_streaming_job_releases")}
    )
    for table in (
        "dw_stream_connection_profiles",
        "dw_stream_schema_contracts",
        "dw_stream_schema_versions",
        "dw_stream_schema_evolution_audits",
        "dw_stream_deployment_groups",
        "dw_stream_deployment_group_members",
        "dw_stream_pipeline_slo_policies",
    ):
        assert inspector.has_table(table)
    assert {
        "security_domain",
        "runtime_version",
        "checkpoint_backend",
        "custom_dependencies",
        "capacity_slots",
        "allows_stateful",
        "highest_sla_tier",
    }.issubset(
        {
            column["name"]
            for column in inspector.get_columns("dw_stream_deployment_groups")
        }
    )


def test_migrate_streaming_job_history_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import migrate_dw_streaming_jobs, migrate_dw_streaming_job_history

    _load_models()
    import app.api.streaming  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_streaming_job_history(eng)
    migrate_dw_streaming_job_history(eng)
    assert inspect(eng).has_table("dw_streaming_job_history")


def test_migrate_streaming_jobs_flink_submit_mode_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import migrate_dw_streaming_jobs, migrate_dw_streaming_jobs_flink_submit_mode

    _load_models()
    import app.api.streaming  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_streaming_jobs_flink_submit_mode(eng)
    migrate_dw_streaming_jobs_flink_submit_mode(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_jobs")}
    assert "flink_sql_submit_mode" in cols
    assert "flink_application_cluster_id" in cols
    assert "flink_application_jm_rest" in cols


def test_migrate_streaming_jobs_submit_audit_and_history_submit_mode_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import (
        migrate_dw_streaming_jobs,
        migrate_dw_streaming_job_history,
        migrate_dw_streaming_jobs_submit_audit_and_history_submit_mode,
    )

    _load_models()
    import app.api.streaming  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_streaming_job_history(eng)
    migrate_dw_streaming_jobs_submit_audit_and_history_submit_mode(eng)
    migrate_dw_streaming_jobs_submit_audit_and_history_submit_mode(eng)
    jcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_jobs")}
    assert "last_submitted_at" in jcols
    assert "last_submitted_by" in jcols
    hcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_job_history")}
    assert "flink_sql_submit_mode" in hcols


def test_migrate_flink_jar_operator_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import (
        migrate_dw_streaming_jobs,
        migrate_dw_streaming_job_history,
        migrate_dw_streaming_jobs_flink_jar_operator,
    )

    _load_models()
    import app.api.streaming  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_streaming_job_history(eng)
    migrate_dw_streaming_jobs_flink_jar_operator(eng)
    migrate_dw_streaming_jobs_flink_jar_operator(eng)
    jcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_jobs")}
    assert "flink_jar_submit_mode" in jcols
    assert "flink_operator_deployment_name" in jcols
    hcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_job_history")}
    assert "flink_jar_submit_mode" in hcols


def test_migrate_flink_session_profiles_and_job_fk_idempotent():
    from app.core.database import Base
    from app.services.rbac_seed import (
        migrate_dw_streaming_jobs,
        migrate_dw_flink_session_profiles,
        migrate_dw_streaming_jobs_flink_session_profile,
    )

    _load_models()
    import app.api.streaming  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_flink_session_profiles(eng)
    migrate_dw_flink_session_profiles(eng)
    migrate_dw_streaming_jobs_flink_session_profile(eng)
    migrate_dw_streaming_jobs_flink_session_profile(eng)
    assert inspect(eng).has_table("dw_flink_session_profiles")
    jcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_jobs")}
    assert "flink_session_profile_id" in jcols


def test_migrate_streaming_job_history_ensure_columns_idempotent():
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
    from app.core.database import Base
    from sqlalchemy import text
    from app.services.rbac_seed import (
        migrate_dw_streaming_jobs,
        migrate_dw_streaming_job_history,
        migrate_dw_streaming_job_history_ensure_columns,
    )

    _load_models()
    import app.api.streaming  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_streaming_job_history(eng)
    migrate_dw_streaming_job_history_ensure_columns(eng)
    migrate_dw_streaming_job_history_ensure_columns(eng)
    hcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_job_history")}
    assert "streaming_properties" in hcols
    assert "flink_sql_submit_mode" in hcols
    assert "flink_jar_submit_mode" in hcols

    # 旧瘦表：仅基础列，ensure 应补齐
    eng2 = _fresh_engine()
    with eng2.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE dw_streaming_jobs (
              id INTEGER PRIMARY KEY,
              workspace_id INTEGER,
              name VARCHAR(128),
              job_type VARCHAR(16)
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE dw_streaming_job_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id INTEGER NOT NULL,
              job_type VARCHAR(16) NOT NULL,
              script_content TEXT,
              main_class VARCHAR(256),
              program_args VARCHAR(512),
              parallelism INTEGER,
              saved_at TIMESTAMP NOT NULL,
              saved_by INTEGER
            )
            """
        ))
    migrate_dw_streaming_job_history_ensure_columns(eng2)
    h2 = {c["name"] for c in inspect(eng2).get_columns("dw_streaming_job_history")}
    assert "streaming_properties" in h2
    assert "flink_sql_submit_mode" in h2
    assert "flink_jar_submit_mode" in h2


def test_migrate_streaming_program_args_is_text_on_orm_and_noop_sqlite():
    from sqlalchemy import Text
    from app.api.streaming import StreamingJob, StreamingJobHistory
    from app.services.rbac_seed import migrate_dw_streaming_program_args_widen

    assert isinstance(StreamingJob.__table__.c.program_args.type, Text)
    assert isinstance(StreamingJobHistory.__table__.c.program_args.type, Text)
    eng = _fresh_engine()
    migrate_dw_streaming_program_args_widen(eng)  # sqlite: no-op
    migrate_dw_streaming_program_args_widen(eng)


def test_migrate_node_folders_scope_and_jar_library():
    from app.core.database import Base
    from app.services.rbac_seed import (
        migrate_dw_node_folders_scope,
        migrate_dw_streaming_jobs_folder_sort_and_jar_refs,
        migrate_dw_streaming_jar_library,
        migrate_dw_streaming_jobs,
    )
    from sqlalchemy import text

    _load_models()
    import app.api.streaming  # noqa: F401
    from app.models.workspace import NodeFolder  # noqa: F401

    eng = _fresh_engine()
    Base.metadata.create_all(eng)
    migrate_dw_streaming_jobs(eng)
    migrate_dw_node_folders_scope(eng)
    migrate_dw_node_folders_scope(eng)
    migrate_dw_streaming_jobs_folder_sort_and_jar_refs(eng)
    migrate_dw_streaming_jar_library(eng)
    migrate_dw_streaming_jar_library(eng)
    from app.services.rbac_seed import migrate_dw_streaming_resource_libraries

    migrate_dw_streaming_resource_libraries(eng)
    migrate_dw_streaming_resource_libraries(eng)
    fcols = {c["name"] for c in inspect(eng).get_columns("dw_node_folders")}
    assert "scope" in fcols
    jcols = {c["name"] for c in inspect(eng).get_columns("dw_streaming_jobs")}
    assert "sort_order" in jcols
    assert "jar_artifact_id" in jcols
    assert "jar_version_id" in jcols
    assert "connector_version_ids" in jcols
    assert "dependency_file_version_ids" in jcols
    assert inspect(eng).has_table("dw_streaming_jar_artifacts")
    assert inspect(eng).has_table("dw_streaming_jar_versions")
    assert inspect(eng).has_table("dw_streaming_connector_artifacts")
    assert inspect(eng).has_table("dw_streaming_connector_versions")
    assert inspect(eng).has_table("dw_streaming_file_artifacts")
    assert inspect(eng).has_table("dw_streaming_file_versions")

    # 旧表无 scope：补列
    eng2 = _fresh_engine()
    with eng2.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE dw_node_folders (
              id INTEGER PRIMARY KEY,
              workspace_id INTEGER,
              name VARCHAR(128),
              parent_id INTEGER
            )
            """
        ))
        conn.execute(text(
            """
            CREATE TABLE dw_streaming_jobs (
              id INTEGER PRIMARY KEY,
              workspace_id INTEGER,
              name VARCHAR(128),
              job_type VARCHAR(16)
            )
            """
        ))
    migrate_dw_node_folders_scope(eng2)
    migrate_dw_streaming_jobs_folder_sort_and_jar_refs(eng2)
    assert "scope" in {c["name"] for c in inspect(eng2).get_columns("dw_node_folders")}
    assert "sort_order" in {c["name"] for c in inspect(eng2).get_columns("dw_streaming_jobs")}


def test_dolphin_trigger_prefix_from_command_type():
    from app.services.dolphin_instance_sync import _trigger_prefix_from_ds_command_type

    assert _trigger_prefix_from_ds_command_type("SCHEDULER") == "schedule"
    assert _trigger_prefix_from_ds_command_type("START_PROCESS") == "manual"
    assert _trigger_prefix_from_ds_command_type(None) == "manual"


def test_map_dolphin_process_instance_state():
    from app.services.dolphin import map_dolphin_process_instance_state

    assert map_dolphin_process_instance_state(7) == "success"
    assert map_dolphin_process_instance_state(6) == "failed"
    assert map_dolphin_process_instance_state("SUCCESS") == "success"
    assert map_dolphin_process_instance_state("7") == "success"


def test_parse_dolphin_api_time_shanghai_to_utc_naive():
    from datetime import datetime
    from app.services.dolphin_instance_sync import _parse_dolphin_api_time

    assert _parse_dolphin_api_time("2026-06-15 16:00:00", "Asia/Shanghai") == datetime(2026, 6, 15, 8, 0, 0)


def test_is_manual_development_workflow_run():
    from app.services.workflow_trigger_display import is_manual_development_workflow_run

    assert is_manual_development_workflow_run("manual") is True
    assert is_manual_development_workflow_run("manual|ds:1") is True
    assert is_manual_development_workflow_run("schedule|ds:2") is False
    assert is_manual_development_workflow_run("rerun|ds:3") is False


def test_workflow_trigger_display():
    from app.services.workflow_trigger_display import format_trigger_type_label, parse_dolphin_process_instance_id

    assert parse_dolphin_process_instance_id("manual|ds:12345") == 12345
    assert "12345" in format_trigger_type_label("manual|ds:12345")
    # Dolphin 定时调度：库内 trigger_type 常为 manual|ds:…，需 commandType 才能显示为定时
    lab_sched = format_trigger_type_label("manual|ds:12345", "SCHEDULER")
    assert "12345" in lab_sched
    assert "调度执行" in lab_sched
    assert parse_dolphin_process_instance_id("manual") is None
    lab = format_trigger_type_label("manual")
    assert "开发手动" in lab
    assert "无 Dolphin" in lab or "本地" in lab

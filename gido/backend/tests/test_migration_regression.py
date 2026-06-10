# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""启动迁移函数的幂等回归：不连真实 DB，仅用内存 SQLite + create_all。"""

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

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""初始化权限与内置角色（幂等）。"""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from app.models.rbac_models import Role, Permission
from app.core import perm_codes as P
from app.models.workspace import User


def migrate_dw_users_avatar(engine: Engine) -> None:
    """User：头像 preset/upload 引用。"""
    insp = inspect(engine)
    if not insp.has_table("dw_users"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_users")}
    if "avatar" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_users ADD COLUMN avatar VARCHAR(256) NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_users ADD COLUMN avatar VARCHAR(256)"))


def migrate_scheduler_engine_fields(engine: Engine) -> None:
    """调度引擎抽象字段：GIDO 持有定义/实例，Dolphin 仅作为隐藏执行引擎。"""
    insp = inspect(engine)
    table_cols = {
        table: {c["name"] for c in insp.get_columns(table)}
        for table in (
            "dw_workflows",
            "dw_workflow_instances",
            "dw_node_instances",
            "dw_backfill_requests",
            "dw_alert_events",
            "dw_alert_notification_configs",
        )
        if insp.has_table(table)
    }

    def add_column(conn, table: str, name: str, ddl: str) -> None:
        if table in table_cols and name not in table_cols.get(table, set()):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    with engine.begin() as conn:
        add_column(conn, "dw_workflows", "scheduler_engine", "VARCHAR(32)")
        add_column(conn, "dw_workflows", "scheduler_definition_id", "VARCHAR(128)")
        add_column(conn, "dw_workflows", "scheduler_project_id", "VARCHAR(128)")
        add_column(conn, "dw_workflows", "status", "VARCHAR(32)")
        add_column(conn, "dw_workflows", "active_version_id", "INTEGER")
        add_column(conn, "dw_workflow_instances", "job_version_id", "INTEGER")
        add_column(conn, "dw_workflow_instances", "backfill_request_id", "INTEGER")
        add_column(conn, "dw_workflow_instances", "scheduler_engine", "VARCHAR(32)")
        add_column(conn, "dw_workflow_instances", "scheduler_project_id", "VARCHAR(128)")
        add_column(conn, "dw_workflow_instances", "scheduler_definition_id", "VARCHAR(128)")
        add_column(conn, "dw_workflow_instances", "scheduler_definition_version", "INTEGER")
        add_column(conn, "dw_workflow_instances", "scheduler_instance_id", "VARCHAR(128)")
        add_column(conn, "dw_workflow_instances", "scheduler_run_key", "VARCHAR(128)")
        add_column(conn, "dw_workflow_instances", "scheduler_state_raw", "VARCHAR(128)")
        add_column(conn, "dw_workflow_instances", "scheduler_error", "TEXT")
        add_column(conn, "dw_workflow_instances", "last_synced_at", "TIMESTAMP")
        add_column(conn, "dw_node_instances", "scheduler_engine", "VARCHAR(32)")
        add_column(conn, "dw_node_instances", "scheduler_project_id", "VARCHAR(128)")
        add_column(conn, "dw_node_instances", "scheduler_definition_id", "VARCHAR(128)")
        add_column(conn, "dw_node_instances", "scheduler_instance_id", "VARCHAR(128)")
        add_column(conn, "dw_node_instances", "scheduler_task_instance_id", "VARCHAR(128)")
        add_column(conn, "dw_node_instances", "scheduler_task_code", "VARCHAR(128)")
        add_column(conn, "dw_node_instances", "scheduler_state_raw", "VARCHAR(128)")
        add_column(conn, "dw_node_instances", "scheduler_error", "TEXT")
        add_column(conn, "dw_node_instances", "last_synced_at", "TIMESTAMP")
        add_column(conn, "dw_backfill_requests", "total_instances", "INTEGER")
        add_column(conn, "dw_backfill_requests", "succeeded_instances", "INTEGER")
        add_column(conn, "dw_backfill_requests", "failed_instances", "INTEGER")
        add_column(conn, "dw_backfill_requests", "running_instances", "INTEGER")
        add_column(conn, "dw_backfill_requests", "submit_mode", "VARCHAR(32)")
        add_column(conn, "dw_backfill_requests", "failure_reason", "TEXT")
        add_column(conn, "dw_alert_events", "severity", "VARCHAR(16)")
        add_column(conn, "dw_alert_events", "dedupe_key", "VARCHAR(256)")
        add_column(conn, "dw_alert_events", "assignee_id", "INTEGER")
        add_column(conn, "dw_alert_events", "assignee_group", "VARCHAR(128)")
        add_column(conn, "dw_alert_events", "notification_status", "VARCHAR(32)")
        if not insp.has_table("dw_alert_notification_configs"):
            conn.execute(text(
                "CREATE TABLE dw_alert_notification_configs ("
                "workspace_id INTEGER PRIMARY KEY, "
                "enabled BOOLEAN DEFAULT FALSE NOT NULL, "
                "min_severity VARCHAR(16) DEFAULT 'error' NOT NULL, "
                "email_enabled BOOLEAN DEFAULT FALSE NOT NULL, "
                "email_to TEXT, "
                "smtp_host VARCHAR(256), "
                "smtp_port INTEGER, "
                "smtp_user VARCHAR(256), "
                "smtp_password TEXT, "
                "smtp_from VARCHAR(256), "
                "smtp_tls BOOLEAN DEFAULT FALSE NOT NULL, "
                "webhook_enabled BOOLEAN DEFAULT FALSE NOT NULL, "
                "webhook_url TEXT, "
                "lark_enabled BOOLEAN DEFAULT FALSE NOT NULL, "
                "lark_webhook_url TEXT, "
                "wecom_enabled BOOLEAN DEFAULT FALSE NOT NULL, "
                "wecom_webhook_url TEXT, "
                "updated_at TIMESTAMP, "
                "updated_by INTEGER"
                ")"
            ))

        if insp.has_table("dw_workflows"):
            conn.execute(text("UPDATE dw_workflows SET scheduler_engine = 'dolphin' WHERE scheduler_engine IS NULL"))
            conn.execute(text("UPDATE dw_workflows SET status = CASE WHEN scheduler_definition_id IS NULL THEN 'draft' ELSE 'published' END WHERE status IS NULL"))
        if insp.has_table("dw_workflow_instances"):
            conn.execute(text("UPDATE dw_workflow_instances SET scheduler_engine = 'dolphin' WHERE scheduler_engine IS NULL"))
        if insp.has_table("dw_node_instances"):
            conn.execute(text("UPDATE dw_node_instances SET scheduler_engine = 'dolphin' WHERE scheduler_engine IS NULL"))
        if insp.has_table("dw_backfill_requests"):
            conn.execute(text("UPDATE dw_backfill_requests SET total_instances = 0 WHERE total_instances IS NULL"))
            conn.execute(text("UPDATE dw_backfill_requests SET succeeded_instances = 0 WHERE succeeded_instances IS NULL"))
            conn.execute(text("UPDATE dw_backfill_requests SET failed_instances = 0 WHERE failed_instances IS NULL"))
            conn.execute(text("UPDATE dw_backfill_requests SET running_instances = 0 WHERE running_instances IS NULL"))
            conn.execute(text("UPDATE dw_backfill_requests SET submit_mode = 'daily' WHERE submit_mode IS NULL"))
        if insp.has_table("dw_alert_events"):
            conn.execute(text("UPDATE dw_alert_events SET severity = COALESCE(severity, level, 'warning') WHERE severity IS NULL"))
            conn.execute(text("UPDATE dw_alert_events SET notification_status = 'pending' WHERE notification_status IS NULL"))
        if insp.has_table("dw_workflow_instances"):
            if insp.has_table("dw_job_versions"):
                if engine.dialect.name == "postgresql":
                    conn.execute(text(
                        "UPDATE dw_workflow_instances wi SET "
                        "scheduler_project_id = COALESCE(wi.scheduler_project_id, jv.scheduler_project_id), "
                        "scheduler_definition_id = COALESCE(wi.scheduler_definition_id, jv.scheduler_definition_id), "
                        "scheduler_definition_version = COALESCE(wi.scheduler_definition_version, jv.version_no) "
                        "FROM dw_job_versions jv WHERE wi.job_version_id = jv.id"
                    ))
                else:
                    conn.execute(text(
                        "UPDATE dw_workflow_instances SET "
                        "scheduler_project_id = COALESCE(scheduler_project_id, (SELECT scheduler_project_id FROM dw_job_versions WHERE id = dw_workflow_instances.job_version_id)), "
                        "scheduler_definition_id = COALESCE(scheduler_definition_id, (SELECT scheduler_definition_id FROM dw_job_versions WHERE id = dw_workflow_instances.job_version_id)), "
                        "scheduler_definition_version = COALESCE(scheduler_definition_version, (SELECT version_no FROM dw_job_versions WHERE id = dw_workflow_instances.job_version_id)) "
                        "WHERE job_version_id IS NOT NULL"
                    ))
            concat_expr = (
                "COALESCE(scheduler_engine, '') || ':' || COALESCE(scheduler_project_id, '') || ':' || "
                "COALESCE(scheduler_definition_id, '') || ':' || COALESCE(scheduler_instance_id, '')"
            )
            if engine.dialect.name == "mysql":
                concat_expr = (
                    "CONCAT(COALESCE(scheduler_engine, ''), ':', COALESCE(scheduler_project_id, ''), ':', "
                    "COALESCE(scheduler_definition_id, ''), ':', COALESCE(scheduler_instance_id, ''))"
                )
            conn.execute(text(
                "UPDATE dw_workflow_instances SET scheduler_run_key = "
                f"COALESCE(scheduler_run_key, {concat_expr}) "
                "WHERE scheduler_instance_id IS NOT NULL"
            ))
        if insp.has_table("dw_node_instances") and insp.has_table("dw_workflow_instances"):
            if engine.dialect.name == "postgresql":
                conn.execute(text(
                    "UPDATE dw_node_instances ni SET "
                    "scheduler_project_id = COALESCE(ni.scheduler_project_id, wi.scheduler_project_id), "
                    "scheduler_definition_id = COALESCE(ni.scheduler_definition_id, wi.scheduler_definition_id), "
                    "scheduler_instance_id = COALESCE(ni.scheduler_instance_id, wi.scheduler_instance_id) "
                    "FROM dw_workflow_instances wi WHERE ni.workflow_instance_id = wi.id"
                ))
            else:
                conn.execute(text(
                    "UPDATE dw_node_instances SET "
                    "scheduler_project_id = COALESCE(scheduler_project_id, (SELECT scheduler_project_id FROM dw_workflow_instances WHERE id = dw_node_instances.workflow_instance_id)), "
                    "scheduler_definition_id = COALESCE(scheduler_definition_id, (SELECT scheduler_definition_id FROM dw_workflow_instances WHERE id = dw_node_instances.workflow_instance_id)), "
                    "scheduler_instance_id = COALESCE(scheduler_instance_id, (SELECT scheduler_instance_id FROM dw_workflow_instances WHERE id = dw_node_instances.workflow_instance_id)) "
                    "WHERE workflow_instance_id IS NOT NULL"
                ))
        try:
            if insp.has_table("dw_workflows"):
                if engine.dialect.name == "postgresql":
                    conn.execute(text(
                        "UPDATE dw_workflows SET "
                        "scheduler_definition_id = COALESCE(scheduler_definition_id, dag_config->>'ds_process_code'), "
                        "scheduler_project_id = COALESCE(scheduler_project_id, dag_config->>'ds_project_code') "
                        "WHERE dag_config IS NOT NULL"
                    ))
                elif engine.dialect.name == "mysql":
                    conn.execute(text(
                        "UPDATE dw_workflows SET "
                        "scheduler_definition_id = COALESCE(scheduler_definition_id, JSON_UNQUOTE(JSON_EXTRACT(dag_config, '$.ds_process_code'))), "
                        "scheduler_project_id = COALESCE(scheduler_project_id, JSON_UNQUOTE(JSON_EXTRACT(dag_config, '$.ds_project_code'))) "
                        "WHERE dag_config IS NOT NULL"
                    ))
                elif engine.dialect.name == "sqlite":
                    conn.execute(text(
                        "UPDATE dw_workflows SET "
                        "scheduler_definition_id = COALESCE(scheduler_definition_id, json_extract(dag_config, '$.ds_process_code')), "
                        "scheduler_project_id = COALESCE(scheduler_project_id, json_extract(dag_config, '$.ds_project_code')) "
                        "WHERE dag_config IS NOT NULL"
                    ))
        except Exception:
            # 旧 SQLite 未启用 JSON1 或部分方言 JSON 函数不可用时，不阻断启动；业务层仍会回退 dag_config。
            pass
        try:
            if insp.has_table("dw_workflow_instances"):
                if engine.dialect.name == "postgresql":
                    conn.execute(text(
                        "UPDATE dw_workflow_instances SET scheduler_instance_id = "
                        "COALESCE(scheduler_instance_id, substring(trigger_type from 'ds:([0-9]+)')) "
                        "WHERE trigger_type LIKE '%ds:%'"
                    ))
                elif engine.dialect.name == "mysql":
                    conn.execute(text(
                        "UPDATE dw_workflow_instances SET scheduler_instance_id = "
                        "COALESCE(scheduler_instance_id, SUBSTRING_INDEX(SUBSTRING_INDEX(trigger_type, 'ds:', -1), '|', 1)) "
                        "WHERE trigger_type LIKE '%ds:%'"
                    ))
                elif engine.dialect.name == "sqlite":
                    conn.execute(text(
                        "UPDATE dw_workflow_instances SET scheduler_instance_id = "
                        "COALESCE(scheduler_instance_id, substr(trigger_type, instr(trigger_type, 'ds:') + 3)) "
                        "WHERE trigger_type LIKE '%ds:%'"
                    ))
        except Exception:
            pass


def migrate_schema(engine: Engine) -> None:
    """SQLite：为已存在的 dw_users 表补齐 role_id 列。"""
    if engine.dialect.name != "sqlite":
        return
    insp = inspect(engine)
    if not insp.has_table("dw_users"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_users")}
    if "role_id" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE dw_users ADD COLUMN role_id INTEGER"))


def migrate_platform_integration(engine: Engine) -> None:
    """创建 dw_platform_integration 表并保证存在 id=1（SQLAlchemy 可能已建表但未插行）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_platform_integration"):
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_platform_integration (
                            id INT NOT NULL PRIMARY KEY,
                            ds_enabled TINYINT(1) NULL,
                            ds_url VARCHAR(512) NULL,
                            ds_ui_url VARCHAR(512) NULL,
                            ds_token TEXT NULL,
                            ds_project_name VARCHAR(128) NULL,
                            updated_at DATETIME NULL
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
            elif engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_platform_integration (
                            id INTEGER NOT NULL PRIMARY KEY,
                            ds_enabled BOOLEAN NULL,
                            ds_url VARCHAR(512) NULL,
                            ds_ui_url VARCHAR(512) NULL,
                            ds_token TEXT NULL,
                            ds_project_name VARCHAR(128) NULL,
                            updated_at TIMESTAMP NULL
                        )
                        """
                    )
                )
            else:
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_platform_integration (
                            id INTEGER NOT NULL PRIMARY KEY,
                            ds_enabled BOOLEAN,
                            ds_url VARCHAR(512),
                            ds_ui_url VARCHAR(512),
                            ds_token TEXT,
                            ds_project_name VARCHAR(128),
                            updated_at TIMESTAMP
                        )
                        """
                    )
                )
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("INSERT IGNORE INTO dw_platform_integration (id) VALUES (1)"))
        elif engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "INSERT INTO dw_platform_integration (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
                )
            )
        else:
            conn.execute(text("INSERT OR IGNORE INTO dw_platform_integration (id) VALUES (1)"))


def migrate_platform_integration_flink(engine: Engine) -> None:
    """为已存在的 dw_platform_integration 表补齐 Flink 可插拔字段（幂等）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_platform_integration"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_platform_integration")}
    flink_cols = [
        ("flink_url", "VARCHAR(512) NULL"),
        ("flink_sql_gateway_url", "VARCHAR(512) NULL"),
        ("flink_gateway_jobmanager_rest_url", "VARCHAR(512) NULL"),
        ("flink_ui_url", "VARCHAR(512) NULL"),
        ("flink_k8s_application_image", "VARCHAR(512) NULL"),
        ("flink_k8s_namespace", "VARCHAR(256) NULL"),
        ("flink_k8s_application_jm_rest_template", "VARCHAR(1024) NULL"),
        ("flink_k8s_cluster_domain", "VARCHAR(256) NULL"),
        ("flink_k8s_apiserver_fallback_url", "VARCHAR(512) NULL"),
        ("flink_k8s_jm_rpc_host", "VARCHAR(512) NULL"),
        ("flink_k8s_sql_gateway_rest_host", "VARCHAR(512) NULL"),
    ]
    with engine.begin() as conn:
        for name, ddl in flink_cols:
            if name in cols:
                continue
            if engine.dialect.name == "mysql":
                conn.execute(text(f"ALTER TABLE dw_platform_integration ADD COLUMN {name} {ddl}"))
            else:
                if name == "flink_k8s_application_jm_rest_template":
                    conn.execute(text(f"ALTER TABLE dw_platform_integration ADD COLUMN {name} VARCHAR(1024)"))
                elif name == "flink_k8s_namespace":
                    conn.execute(text(f"ALTER TABLE dw_platform_integration ADD COLUMN {name} VARCHAR(256)"))
                elif name == "flink_k8s_cluster_domain":
                    conn.execute(text(f"ALTER TABLE dw_platform_integration ADD COLUMN {name} VARCHAR(256)"))
                else:
                    conn.execute(text(f"ALTER TABLE dw_platform_integration ADD COLUMN {name} VARCHAR(512)"))


def migrate_platform_integration_aps_schedule(engine: Engine) -> None:
    """平台级开关：是否允许本地 APScheduler 注册工作流定时（防与 Dolphin 双跑）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_platform_integration"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_platform_integration")}
    if "aps_workflow_schedule_enabled" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE dw_platform_integration ADD COLUMN aps_workflow_schedule_enabled BOOLEAN"))


def migrate_platform_integration_copilot(engine: Engine) -> None:
    """为平台/工作空间集成表补齐 Copilot LLM 可插拔字段（幂等）。"""
    insp = inspect(engine)
    copilot_cols = [
        ("copilot_llm_base_url", "VARCHAR(512) NULL"),
        ("copilot_llm_model", "VARCHAR(128) NULL"),
        ("copilot_llm_api_key", "TEXT NULL"),
    ]
    for table in ("dw_platform_integration", "dw_workspace_platform_integration"):
        if not insp.has_table(table):
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in copilot_cols:
                if name in cols:
                    continue
                if engine.dialect.name == "mysql":
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                elif name == "copilot_llm_api_key":
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} TEXT"))
                elif name == "copilot_llm_model":
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} VARCHAR(128)"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} VARCHAR(512)"))


def migrate_workspace_owner_members(engine: Engine) -> None:
    """为历史工作空间补一条 owner 的成员行（空间角色 admin），与新建空间行为一致。"""
    insp = inspect(engine)
    if not insp.has_table("dw_workspaces") or not insp.has_table("dw_workspace_members"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dw_workspace_members (workspace_id, user_id, role)
                SELECT w.id, w.owner_id, 'admin'
                FROM dw_workspaces w
                WHERE NOT EXISTS (
                    SELECT 1 FROM dw_workspace_members m
                    WHERE m.workspace_id = w.id AND m.user_id = w.owner_id
                )
                """
            )
        )


def migrate_default_workspace_to_infras(engine: Engine) -> None:
    """将历史「默认工作空间」重命名为 infras；与 init_db 默认名一致。若已存在名为 infras 的工作空间则跳过重命名。"""
    insp = inspect(engine)
    if not insp.has_table("dw_workspaces"):
        return
    # 统一用「先 COUNT 再 UPDATE」，避免 MySQL 1093（UPDATE 同表子查询）；各方言行为一致
    with engine.begin() as conn:
        has_infras = conn.execute(
            text("SELECT COUNT(*) FROM dw_workspaces WHERE name = :n"),
            {"n": "infras"},
        ).scalar()
        if int(has_infras or 0) == 0:
            conn.execute(
                text(
                    """
                    UPDATE dw_workspaces
                    SET name = 'infras',
                        description = COALESCE(NULLIF(description, ''), '系统默认工作空间')
                    WHERE name = '默认工作空间'
                    """
                )
            )


def migrate_dw_task_nodes_owner_lock(engine: Engine) -> None:
    """TaskNode：负责人与提交后锁定（对齐 GIDO 脚本治理）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_task_nodes"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_task_nodes")}
    with engine.begin() as conn:
        if "owner_id" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN owner_id INT NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN owner_id INTEGER"))
        if "is_locked" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN is_locked TINYINT(1) NOT NULL DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0"))
    # 回填 owner_id = created_by
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dw_task_nodes SET owner_id = created_by WHERE owner_id IS NULL AND created_by IS NOT NULL")
        )


def migrate_dw_task_nodes_edit_lock(engine: Engine) -> None:
    """TaskNode：协作编辑锁（与发布锁定 is_locked 独立）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_task_nodes"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_task_nodes")}
    with engine.begin() as conn:
        if "edit_lock_user_id" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN edit_lock_user_id INT NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN edit_lock_user_id INTEGER NULL"))
        if "edit_lock_at" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN edit_lock_at DATETIME NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN edit_lock_at TIMESTAMP NULL"))


def migrate_dw_task_nodes_sort_order(engine: Engine) -> None:
    """TaskNode：同目录内展示顺序。列默认 0 = 自然字典序；用户拖拽后写入 >0。"""
    insp = inspect(engine)
    if not insp.has_table("dw_task_nodes"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_task_nodes")}
    with engine.begin() as conn:
        if "sort_order" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN sort_order INT NOT NULL DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE dw_task_nodes ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))


def migrate_sort_order_name_default(engine: Engine) -> None:
    """一次性：清除历史按创建时间回填的 sort_order，恢复默认字典序。"""
    insp = inspect(engine)
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS dw_schema_flags (
                        flag VARCHAR(64) PRIMARY KEY,
                        applied_at TIMESTAMP NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
        elif engine.dialect.name == "mysql":
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS dw_schema_flags (
                        flag VARCHAR(64) PRIMARY KEY,
                        applied_at DATETIME NOT NULL
                    )
                    """
                )
            )
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS dw_schema_flags (
                        flag VARCHAR(64) PRIMARY KEY,
                        applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

        flag = "sort_order_name_default_v1"
        row = conn.execute(
            text("SELECT 1 AS x FROM dw_schema_flags WHERE flag = :f LIMIT 1"),
            {"f": flag},
        ).fetchone()
        if row:
            return

        if insp.has_table("dw_task_nodes"):
            conn.execute(text("UPDATE dw_task_nodes SET sort_order = 0"))
        if insp.has_table("dw_streaming_jobs"):
            cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
            if "sort_order" in cols:
                conn.execute(text("UPDATE dw_streaming_jobs SET sort_order = 0"))

        if engine.dialect.name == "mysql":
            conn.execute(
                text("INSERT INTO dw_schema_flags (flag, applied_at) VALUES (:f, UTC_TIMESTAMP())"),
                {"f": flag},
            )
        else:
            conn.execute(
                text("INSERT INTO dw_schema_flags (flag, applied_at) VALUES (:f, CURRENT_TIMESTAMP)"),
                {"f": flag},
            )


def migrate_dw_workflow_updated_by(engine: Engine) -> None:
    insp = inspect(engine)
    if not insp.has_table("dw_workflows"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_workflows")}
    if "updated_by" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_workflows ADD COLUMN updated_by INT NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_workflows ADD COLUMN updated_by INTEGER NULL"))


def migrate_dw_workflow_instance_submitted_by(engine: Engine) -> None:
    insp = inspect(engine)
    if not insp.has_table("dw_workflow_instances"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_workflow_instances")}
    if "submitted_by" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_workflow_instances ADD COLUMN submitted_by INT NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_workflow_instances ADD COLUMN submitted_by INTEGER NULL"))


def migrate_dw_quality_dolphin_refs(engine: Engine) -> None:
    """质量规则：Dolphin 联动字段。"""
    insp = inspect(engine)
    if not insp.has_table("dw_quality_rules"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_quality_rules")}
    if "dolphin_refs" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN dolphin_refs JSON NULL"))
        elif engine.dialect.name == "postgresql":
            conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN dolphin_refs JSONB NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN dolphin_refs TEXT"))


def migrate_dw_streaming_jobs(engine: Engine) -> None:
    """为 dw_streaming_jobs 幂等扩展列（MySQL / PostgreSQL / SQLite）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    with engine.begin() as conn:
        if "last_submit_error" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN last_submit_error TEXT NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN last_submit_error TEXT"))
        if "owner_id" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN owner_id INT NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN owner_id INTEGER"))
        if "is_locked" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN is_locked TINYINT(1) NOT NULL DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN is_locked BOOLEAN NOT NULL DEFAULT 0"))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dw_streaming_jobs SET owner_id = created_by WHERE owner_id IS NULL AND created_by IS NOT NULL")
        )


def migrate_dw_streaming_release_lifecycle(engine: Engine) -> None:
    """实时作业发布快照、恢复点和生命周期审计（MySQL/PostgreSQL/SQLite 幂等）。"""
    insp = inspect(engine)
    if insp.has_table("dw_publish_approvals"):
        approval_cols = {
            c["name"] for c in insp.get_columns("dw_publish_approvals")
        }
        if "release_id" not in approval_cols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE dw_publish_approvals "
                        "ADD COLUMN release_id INTEGER NULL"
                    )
                )
        approval_indexes = {
            idx["name"] for idx in inspect(engine).get_indexes("dw_publish_approvals")
        }
        if "idx_publish_approval_release_id" not in approval_indexes:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE INDEX idx_publish_approval_release_id "
                        "ON dw_publish_approvals (release_id)"
                    )
                )
    if not insp.has_table("dw_streaming_jobs"):
        return
    job_cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    with engine.begin() as conn:
        if "current_approved_release_id" not in job_cols:
            conn.execute(
                text(
                    "ALTER TABLE dw_streaming_jobs ADD COLUMN "
                    "current_approved_release_id INTEGER NULL"
                )
            )
        if "current_running_release_id" not in job_cols:
            conn.execute(
                text(
                    "ALTER TABLE dw_streaming_jobs ADD COLUMN "
                    "current_running_release_id INTEGER NULL"
                )
            )
        if "lifecycle_state" not in job_cols:
            nullable = " NOT NULL" if engine.dialect.name in ("mysql", "postgresql") else ""
            conn.execute(
                text(
                    "ALTER TABLE dw_streaming_jobs ADD COLUMN lifecycle_state "
                    f"VARCHAR(32){nullable} DEFAULT 'draft'"
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE dw_streaming_jobs
                    SET lifecycle_state = CASE
                        WHEN status = 'running' THEN 'running'
                        WHEN status = 'failed' THEN 'failed'
                        WHEN status IN ('cancelled', 'canceled', 'finished') THEN 'stopped'
                        ELSE 'draft'
                    END
                    WHERE lifecycle_state IS NULL OR lifecycle_state = 'draft'
                    """
                )
            )

    insp = inspect(engine)
    dialect = engine.dialect.name
    if not insp.has_table("dw_streaming_job_releases"):
        if dialect == "mysql":
            ddl = """
                CREATE TABLE dw_streaming_job_releases (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    job_id INT NOT NULL,
                    version INT NOT NULL,
                    job_name VARCHAR(128) NOT NULL,
                    job_type VARCHAR(16) NOT NULL,
                    script_content TEXT NULL,
                    jar_path VARCHAR(512) NULL,
                    main_class VARCHAR(256) NULL,
                    program_args TEXT NULL,
                    parallelism INT NOT NULL DEFAULT 1,
                    streaming_properties TEXT NULL,
                    flink_sql_submit_mode VARCHAR(32) NULL,
                    flink_jar_submit_mode VARCHAR(32) NULL,
                    flink_session_profile_id INT NULL,
                    jar_artifact_id INT NULL,
                    jar_version_id INT NULL,
                    connector_version_ids TEXT NULL,
                    dependency_file_version_ids TEXT NULL,
                    content_hash VARCHAR(64) NOT NULL,
                    release_note TEXT NULL,
                    approval_status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    submitted_by INT NULL,
                    submitted_at DATETIME NOT NULL,
                    approved_by INT NULL,
                    approved_at DATETIME NULL,
                    approval_comment TEXT NULL,
                    CONSTRAINT fk_stream_release_job FOREIGN KEY (job_id)
                        REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE,
                    UNIQUE KEY uq_streaming_release_job_version (job_id, version),
                    INDEX idx_streaming_release_job_submitted (job_id, submitted_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        elif dialect == "postgresql":
            ddl = """
                CREATE TABLE dw_streaming_job_releases (
                    id SERIAL PRIMARY KEY,
                    job_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    job_name VARCHAR(128) NOT NULL,
                    job_type VARCHAR(16) NOT NULL,
                    script_content TEXT,
                    jar_path VARCHAR(512),
                    main_class VARCHAR(256),
                    program_args TEXT,
                    parallelism INTEGER NOT NULL DEFAULT 1,
                    streaming_properties TEXT,
                    flink_sql_submit_mode VARCHAR(32),
                    flink_jar_submit_mode VARCHAR(32),
                    flink_session_profile_id INTEGER,
                    jar_artifact_id INTEGER,
                    jar_version_id INTEGER,
                    connector_version_ids TEXT,
                    dependency_file_version_ids TEXT,
                    content_hash VARCHAR(64) NOT NULL,
                    release_note TEXT,
                    approval_status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    submitted_by INTEGER,
                    submitted_at TIMESTAMP NOT NULL,
                    approved_by INTEGER,
                    approved_at TIMESTAMP,
                    approval_comment TEXT,
                    CONSTRAINT fk_stream_release_job FOREIGN KEY (job_id)
                        REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE,
                    CONSTRAINT uq_streaming_release_job_version UNIQUE (job_id, version)
                )
            """
        else:
            ddl = """
                CREATE TABLE dw_streaming_job_releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    job_name VARCHAR(128) NOT NULL,
                    job_type VARCHAR(16) NOT NULL,
                    script_content TEXT,
                    jar_path VARCHAR(512),
                    main_class VARCHAR(256),
                    program_args TEXT,
                    parallelism INTEGER NOT NULL DEFAULT 1,
                    streaming_properties TEXT,
                    flink_sql_submit_mode VARCHAR(32),
                    flink_jar_submit_mode VARCHAR(32),
                    flink_session_profile_id INTEGER,
                    jar_artifact_id INTEGER,
                    jar_version_id INTEGER,
                    connector_version_ids TEXT,
                    dependency_file_version_ids TEXT,
                    content_hash VARCHAR(64) NOT NULL,
                    release_note TEXT,
                    approval_status VARCHAR(24) NOT NULL DEFAULT 'pending',
                    submitted_by INTEGER,
                    submitted_at TIMESTAMP NOT NULL,
                    approved_by INTEGER,
                    approved_at TIMESTAMP,
                    approval_comment TEXT,
                    CONSTRAINT fk_stream_release_job FOREIGN KEY (job_id)
                        REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE,
                    CONSTRAINT uq_streaming_release_job_version UNIQUE (job_id, version)
                )
            """
        with engine.begin() as conn:
            conn.execute(text(ddl))

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dw_streaming_job_releases "
                "SET approval_status = 'pending' "
                "WHERE approval_status = 'submitted'"
            )
        )

    insp = inspect(engine)
    if not insp.has_table("dw_streaming_restore_points"):
        pk = (
            "INT AUTO_INCREMENT PRIMARY KEY"
            if dialect == "mysql"
            else ("SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT")
        )
        dt = "DATETIME" if dialect == "mysql" else "TIMESTAMP"
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE dw_streaming_restore_points (
                        id {pk},
                        job_id INTEGER NOT NULL,
                        release_id INTEGER NULL,
                        operation_id INTEGER NULL,
                        point_type VARCHAR(24) NOT NULL DEFAULT 'savepoint',
                        status VARCHAR(24) NOT NULL DEFAULT 'pending',
                        path VARCHAR(2048) NULL,
                        flink_job_id VARCHAR(64) NULL,
                        trigger_reason VARCHAR(64) NULL,
                        metadata_json TEXT NULL,
                        error_message TEXT NULL,
                        created_by INTEGER NULL,
                        created_at {dt} NOT NULL,
                        completed_at {dt} NULL,
                        CONSTRAINT fk_stream_restore_job FOREIGN KEY (job_id)
                            REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE,
                        CONSTRAINT fk_stream_restore_release FOREIGN KEY (release_id)
                            REFERENCES dw_streaming_job_releases(id)
                    )
                    """
                )
            )

    insp = inspect(engine)
    if not insp.has_table("dw_streaming_operations"):
        pk = (
            "INT AUTO_INCREMENT PRIMARY KEY"
            if dialect == "mysql"
            else ("SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT")
        )
        dt = "DATETIME" if dialect == "mysql" else "TIMESTAMP"
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE dw_streaming_operations (
                        id {pk},
                        job_id INTEGER NOT NULL,
                        release_id INTEGER NULL,
                        restore_point_id INTEGER NULL,
                        operation_type VARCHAR(32) NOT NULL,
                        status VARCHAR(24) NOT NULL DEFAULT 'pending',
                        idempotency_key VARCHAR(128) NULL,
                        requested_by INTEGER NULL,
                        requested_at {dt} NOT NULL,
                        started_at {dt} NULL,
                        completed_at {dt} NULL,
                        flink_job_id VARCHAR(64) NULL,
                        flink_deployment_name VARCHAR(128) NULL,
                        restore_path VARCHAR(2048) NULL,
                        request_json TEXT NULL,
                        result_json TEXT NULL,
                        error_message TEXT NULL,
                        CONSTRAINT fk_stream_operation_job FOREIGN KEY (job_id)
                            REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE,
                        CONSTRAINT fk_stream_operation_release FOREIGN KEY (release_id)
                            REFERENCES dw_streaming_job_releases(id),
                        CONSTRAINT fk_stream_operation_restore FOREIGN KEY (restore_point_id)
                            REFERENCES dw_streaming_restore_points(id),
                        CONSTRAINT uq_streaming_operation_idempotency UNIQUE (idempotency_key)
                    )
                    """
                )
            )

    # MySQL 在 CREATE TABLE 中已创建 release 索引；其余索引统一按 inspector 幂等补齐。
    index_specs = {
        "dw_streaming_job_releases": (
            "idx_streaming_release_job_submitted",
            "job_id, submitted_at",
        ),
        "dw_streaming_restore_points": (
            "idx_streaming_restore_job_created",
            "job_id, created_at",
        ),
        "dw_streaming_operations": (
            "idx_streaming_operation_job_requested",
            "job_id, requested_at",
        ),
    }
    for table_name, (index_name, columns) in index_specs.items():
        existing = {idx["name"] for idx in inspect(engine).get_indexes(table_name)}
        if index_name not in existing:
            with engine.begin() as conn:
                conn.execute(
                    text(f"CREATE INDEX {index_name} ON {table_name} ({columns})")
                )


def migrate_dw_stream_pipeline(engine: Engine) -> None:
    """Typed pipeline/compiler/schema center foundation (idempotent, cross-dialect)."""
    from app.api.streaming import StreamingJob, StreamingJobRelease
    from app.api.stream_pipeline import (
        StreamConnectionProfile,
        StreamDeploymentGroup,
        StreamDeploymentGroupMember,
        StreamPipelineSloPolicy,
        StreamSchemaContract,
        StreamSchemaEvolutionAudit,
        StreamSchemaVersion,
    )

    insp = inspect(engine)
    json_type = "JSON" if engine.dialect.name in ("mysql", "postgresql") else "JSON"
    columns = {
        "definition_kind": "VARCHAR(32)",
        "pipeline_spec": json_type,
        "compiler_version": "VARCHAR(64)",
        "generated_artifact": json_type,
        "spec_hash": "VARCHAR(64)",
    }
    for table in ("dw_streaming_jobs", "dw_streaming_job_releases"):
        if not insp.has_table(table):
            continue
        existing = {col["name"] for col in inspect(engine).get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            if table == "dw_streaming_jobs":
                conn.execute(
                    text(
                        "UPDATE dw_streaming_jobs SET definition_kind = "
                        "CASE WHEN job_type = 'JAR' THEN 'jar' ELSE 'sql' END "
                        "WHERE definition_kind IS NULL"
                    )
                )
            else:
                conn.execute(
                    text(
                        "UPDATE dw_streaming_job_releases SET definition_kind = "
                        "CASE WHEN job_type = 'JAR' THEN 'jar' ELSE 'sql' END "
                        "WHERE definition_kind IS NULL"
                    )
                )

    # Models are ordered by dependency; checkfirst keeps startup/bootstrap repeatable.
    for model in (
        StreamConnectionProfile,
        StreamSchemaContract,
        StreamSchemaVersion,
        StreamSchemaEvolutionAudit,
        StreamDeploymentGroup,
        StreamDeploymentGroupMember,
        StreamPipelineSloPolicy,
    ):
        model.__table__.create(bind=engine, checkfirst=True)

    if inspect(engine).has_table("dw_stream_deployment_groups"):
        group_columns = {
            col["name"]
            for col in inspect(engine).get_columns("dw_stream_deployment_groups")
        }
        group_additions = {
            "security_domain": "VARCHAR(64) DEFAULT 'default'",
            "runtime_version": "VARCHAR(32) DEFAULT '2.0.1'",
            "checkpoint_backend": "VARCHAR(64) DEFAULT 'filesystem'",
            "custom_dependencies": json_type,
            "capacity_slots": "INTEGER DEFAULT 4",
            "allows_stateful": (
                "BOOLEAN DEFAULT TRUE"
                if engine.dialect.name == "postgresql"
                else "BOOLEAN DEFAULT 1"
            ),
            "highest_sla_tier": "VARCHAR(32) DEFAULT 'standard'",
        }
        with engine.begin() as conn:
            for name, ddl in group_additions.items():
                if name not in group_columns:
                    conn.execute(
                        text(
                            f"ALTER TABLE dw_stream_deployment_groups "
                            f"ADD COLUMN {name} {ddl}"
                        )
                    )


def migrate_dw_streaming_jobs_streaming_properties(engine: Engine) -> None:
    """Flink SQL Gateway 会话级参数调优（JSON 对象字符串，合并进 Open Session properties）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    if "streaming_properties" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN streaming_properties TEXT NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN streaming_properties TEXT"))


def migrate_dw_streaming_jobs_flink_submit_mode(engine: Engine) -> None:
    """实时作业：SQL 提交模式（session=连已有 JM；kubernetes_application=Gateway v4 脚本起独立集群）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    with engine.begin() as conn:
        if "flink_sql_submit_mode" not in cols:
            if engine.dialect.name in ("mysql", "postgresql"):
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_jobs ADD COLUMN flink_sql_submit_mode "
                        "VARCHAR(32) NOT NULL DEFAULT 'session'"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_jobs ADD COLUMN flink_sql_submit_mode "
                        "VARCHAR(32) DEFAULT 'session'"
                    )
                )
                conn.execute(text("UPDATE dw_streaming_jobs SET flink_sql_submit_mode = 'session' WHERE flink_sql_submit_mode IS NULL"))
        if "flink_application_cluster_id" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN flink_application_cluster_id VARCHAR(256) NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN flink_application_cluster_id VARCHAR(256)"))
        if "flink_application_jm_rest" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN flink_application_jm_rest VARCHAR(512) NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN flink_application_jm_rest VARCHAR(512)"))


def migrate_dw_streaming_jobs_flink_jar_operator(engine: Engine) -> None:
    """JAR 提交模式（session=Session JM；flink_operator=FlinkDeployment CR）及 Operator 部署名。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    with engine.begin() as conn:
        if "flink_jar_submit_mode" not in cols:
            if engine.dialect.name in ("mysql", "postgresql"):
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_jobs ADD COLUMN flink_jar_submit_mode "
                        "VARCHAR(32) NOT NULL DEFAULT 'session'"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_jobs ADD COLUMN flink_jar_submit_mode "
                        "VARCHAR(32) DEFAULT 'session'"
                    )
                )
                conn.execute(
                    text(
                        "UPDATE dw_streaming_jobs SET flink_jar_submit_mode = 'session' "
                        "WHERE flink_jar_submit_mode IS NULL"
                    )
                )
        if "flink_operator_deployment_name" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_jobs ADD COLUMN flink_operator_deployment_name "
                        "VARCHAR(128) NULL"
                    )
                )
            else:
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_jobs ADD COLUMN flink_operator_deployment_name "
                        "VARCHAR(128)"
                    )
                )
    if insp.has_table("dw_streaming_job_history"):
        hcols = {c["name"] for c in insp.get_columns("dw_streaming_job_history")}
        if "flink_jar_submit_mode" not in hcols:
            with engine.begin() as conn:
                if engine.dialect.name == "mysql":
                    conn.execute(
                        text(
                            "ALTER TABLE dw_streaming_job_history ADD COLUMN flink_jar_submit_mode "
                            "VARCHAR(32) NULL"
                        )
                    )
                else:
                    conn.execute(
                        text(
                            "ALTER TABLE dw_streaming_job_history ADD COLUMN flink_jar_submit_mode "
                            "VARCHAR(32)"
                        )
                    )


def migrate_dw_streaming_jobs_submit_audit_and_history_submit_mode(engine: Engine) -> None:
    """实时作业：最近提交审计列；版本快照中记录当时的 SQL 提交模式（便于排障与合规）。"""
    insp = inspect(engine)
    if insp.has_table("dw_streaming_jobs"):
        cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
        with engine.begin() as conn:
            if "last_submitted_at" not in cols:
                if engine.dialect.name == "mysql":
                    conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN last_submitted_at DATETIME NULL"))
                else:
                    conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN last_submitted_at TIMESTAMP"))
            if "last_submitted_by" not in cols:
                if engine.dialect.name == "mysql":
                    conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN last_submitted_by INT NULL"))
                else:
                    conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN last_submitted_by INTEGER"))
    if insp.has_table("dw_streaming_job_history"):
        hcols = {c["name"] for c in insp.get_columns("dw_streaming_job_history")}
        if "flink_sql_submit_mode" in hcols:
            return
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        "ALTER TABLE dw_streaming_job_history ADD COLUMN flink_sql_submit_mode VARCHAR(32) NULL"
                    )
                )
            else:
                conn.execute(text("ALTER TABLE dw_streaming_job_history ADD COLUMN flink_sql_submit_mode VARCHAR(32)"))


def migrate_dw_streaming_job_history_streaming_properties(engine: Engine) -> None:
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_job_history"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_job_history")}
    if "streaming_properties" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_streaming_job_history ADD COLUMN streaming_properties TEXT NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_streaming_job_history ADD COLUMN streaming_properties TEXT"))


def migrate_dw_streaming_job_history_ensure_columns(engine: Engine) -> None:
    """
    补齐 dw_streaming_job_history 与 ORM 一致的列。
    旧库若只建了基础表、后续增量迁移未跑全，create_history=true 写快照会 500。
    """
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_job_history"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_job_history")}
    # name -> (mysql ddl, other ddl)
    needed = {
        "streaming_properties": ("TEXT NULL", "TEXT"),
        "flink_sql_submit_mode": ("VARCHAR(32) NULL", "VARCHAR(32)"),
        "flink_jar_submit_mode": ("VARCHAR(32) NULL", "VARCHAR(32)"),
    }
    missing = [n for n in needed if n not in cols]
    if not missing:
        return
    with engine.begin() as conn:
        for name in missing:
            mysql_t, other_t = needed[name]
            typ = mysql_t if engine.dialect.name == "mysql" else other_t
            conn.execute(text(f"ALTER TABLE dw_streaming_job_history ADD COLUMN {name} {typ}"))


def migrate_dw_streaming_program_args_widen(engine: Engine) -> None:
    """
    JAR program_args：VARCHAR(512) → TEXT。
    多 broker / 长 CLI 参数常见远超 512，否则 create_history / 保存会 StringDataRightTruncation。
    """
    import logging

    _log = logging.getLogger(__name__)
    if engine.dialect.name not in ("mysql", "postgresql"):
        return

    def _needs_text(table: str) -> bool:
        insp = inspect(engine)
        if not insp.has_table(table):
            return False
        col = next((c for c in insp.get_columns(table) if c["name"] == "program_args"), None)
        if not col:
            return False
        length = getattr(col["type"], "length", None)
        # TEXT / CLOB：length 为 None；仍是 VARCHAR(n) 则需加宽
        return length is not None

    jobs = _needs_text("dw_streaming_jobs")
    hist = _needs_text("dw_streaming_job_history")
    if not jobs and not hist:
        return
    try:
        with engine.begin() as conn:
            if jobs:
                if engine.dialect.name == "mysql":
                    conn.execute(text("ALTER TABLE dw_streaming_jobs MODIFY COLUMN program_args TEXT NULL"))
                else:
                    conn.execute(text("ALTER TABLE dw_streaming_jobs ALTER COLUMN program_args TYPE TEXT"))
            if hist:
                if engine.dialect.name == "mysql":
                    conn.execute(text("ALTER TABLE dw_streaming_job_history MODIFY COLUMN program_args TEXT NULL"))
                else:
                    conn.execute(text("ALTER TABLE dw_streaming_job_history ALTER COLUMN program_args TYPE TEXT"))
    except Exception as e:
        _log.warning("migrate_dw_streaming_program_args_widen: %s", e)


def migrate_dw_node_folders_scope(engine: Engine) -> None:
    """文件夹 scope：batch | stream，存量默认 batch。"""
    insp = inspect(engine)
    if not insp.has_table("dw_node_folders"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_node_folders")}
    if "scope" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_node_folders ADD COLUMN scope VARCHAR(16) NOT NULL DEFAULT 'batch'"))
        else:
            conn.execute(text("ALTER TABLE dw_node_folders ADD COLUMN scope VARCHAR(16) DEFAULT 'batch'"))
            conn.execute(text("UPDATE dw_node_folders SET scope = 'batch' WHERE scope IS NULL"))


def migrate_dw_node_folders_sort_order(engine: Engine) -> None:
    """目录同级 sort_order：0=字典序，>0=用户拖拽序。"""
    insp = inspect(engine)
    if not insp.has_table("dw_node_folders"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_node_folders")}
    if "sort_order" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_node_folders ADD COLUMN sort_order INT NOT NULL DEFAULT 0"))
        else:
            conn.execute(text("ALTER TABLE dw_node_folders ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"))


def migrate_dw_streaming_jobs_folder_sort_and_jar_refs(engine: Engine) -> None:
    """实时作业：同目录 sort_order + JAR 制品库外键列。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    with engine.begin() as conn:
        if "sort_order" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN sort_order INT NOT NULL DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN sort_order INTEGER DEFAULT 0"))
                conn.execute(text("UPDATE dw_streaming_jobs SET sort_order = 0 WHERE sort_order IS NULL"))
        if "jar_artifact_id" not in cols:
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN jar_artifact_id INTEGER"))
        if "jar_version_id" not in cols:
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN jar_version_id INTEGER"))


def migrate_dw_streaming_jar_library(engine: Engine) -> None:
    """工作空间级 JAR 制品库 + 版本表。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jar_artifacts"):
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_streaming_jar_artifacts (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            workspace_id INT NOT NULL,
                            name VARCHAR(128) NOT NULL,
                            description TEXT NULL,
                            owner_id INT NULL,
                            created_by INT NULL,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL,
                            INDEX idx_sja_ws (workspace_id),
                            CONSTRAINT fk_sja_ws FOREIGN KEY (workspace_id)
                                REFERENCES dw_workspaces(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
            elif engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_streaming_jar_artifacts (
                            id SERIAL PRIMARY KEY,
                            workspace_id INTEGER NOT NULL REFERENCES dw_workspaces(id) ON DELETE CASCADE,
                            name VARCHAR(128) NOT NULL,
                            description TEXT,
                            owner_id INTEGER,
                            created_by INTEGER,
                            created_at TIMESTAMP NOT NULL,
                            updated_at TIMESTAMP NOT NULL
                        )
                        """
                    )
                )
                conn.execute(text("CREATE INDEX idx_sja_ws ON dw_streaming_jar_artifacts (workspace_id)"))
            else:
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_streaming_jar_artifacts (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            workspace_id INTEGER NOT NULL,
                            name VARCHAR(128) NOT NULL,
                            description TEXT,
                            owner_id INTEGER,
                            created_by INTEGER,
                            created_at TIMESTAMP NOT NULL,
                            updated_at TIMESTAMP NOT NULL
                        )
                        """
                    )
                )
    if not insp.has_table("dw_streaming_jar_versions"):
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_streaming_jar_versions (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            artifact_id INT NOT NULL,
                            version INT NOT NULL,
                            file_name VARCHAR(256) NOT NULL,
                            size_bytes BIGINT NULL,
                            sha256 VARCHAR(64) NULL,
                            storage_key VARCHAR(512) NULL,
                            default_main_class VARCHAR(256) NULL,
                            change_note TEXT NULL,
                            status VARCHAR(16) NOT NULL DEFAULT 'active',
                            uploaded_by INT NULL,
                            uploaded_at DATETIME NOT NULL,
                            UNIQUE KEY uq_sjv_art_ver (artifact_id, version),
                            INDEX idx_sjv_art (artifact_id),
                            CONSTRAINT fk_sjv_art FOREIGN KEY (artifact_id)
                                REFERENCES dw_streaming_jar_artifacts(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
            elif engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_streaming_jar_versions (
                            id SERIAL PRIMARY KEY,
                            artifact_id INTEGER NOT NULL REFERENCES dw_streaming_jar_artifacts(id) ON DELETE CASCADE,
                            version INTEGER NOT NULL,
                            file_name VARCHAR(256) NOT NULL,
                            size_bytes BIGINT,
                            sha256 VARCHAR(64),
                            storage_key VARCHAR(512),
                            default_main_class VARCHAR(256),
                            change_note TEXT,
                            status VARCHAR(16) NOT NULL DEFAULT 'active',
                            uploaded_by INTEGER,
                            uploaded_at TIMESTAMP NOT NULL,
                            UNIQUE (artifact_id, version)
                        )
                        """
                    )
                )
                conn.execute(text("CREATE INDEX idx_sjv_art ON dw_streaming_jar_versions (artifact_id)"))
            else:
                conn.execute(
                    text(
                        """
                        CREATE TABLE dw_streaming_jar_versions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            artifact_id INTEGER NOT NULL,
                            version INTEGER NOT NULL,
                            file_name VARCHAR(256) NOT NULL,
                            size_bytes INTEGER,
                            sha256 VARCHAR(64),
                            storage_key VARCHAR(512),
                            default_main_class VARCHAR(256),
                            change_note TEXT,
                            status VARCHAR(16) NOT NULL DEFAULT 'active',
                            uploaded_by INTEGER,
                            uploaded_at TIMESTAMP NOT NULL,
                            UNIQUE (artifact_id, version)
                        )
                        """
                    )
                )


def _create_streaming_artifact_tables(
    engine: Engine,
    *,
    artifacts_table: str,
    versions_table: str,
    art_idx: str,
    ver_idx: str,
    fk_ws: str,
    fk_art: str,
    uq_ver: str,
) -> None:
    """通用：制品表 + 版本表（连接器 / 依赖文件镜像 JAR）。"""
    insp = inspect(engine)
    if not insp.has_table(artifacts_table):
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {artifacts_table} (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            workspace_id INT NOT NULL,
                            name VARCHAR(128) NOT NULL,
                            description TEXT NULL,
                            owner_id INT NULL,
                            created_by INT NULL,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL,
                            INDEX {art_idx} (workspace_id),
                            CONSTRAINT {fk_ws} FOREIGN KEY (workspace_id)
                                REFERENCES dw_workspaces(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
            elif engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {artifacts_table} (
                            id SERIAL PRIMARY KEY,
                            workspace_id INTEGER NOT NULL REFERENCES dw_workspaces(id) ON DELETE CASCADE,
                            name VARCHAR(128) NOT NULL,
                            description TEXT,
                            owner_id INTEGER,
                            created_by INTEGER,
                            created_at TIMESTAMP NOT NULL,
                            updated_at TIMESTAMP NOT NULL
                        )
                        """
                    )
                )
                conn.execute(text(f"CREATE INDEX {art_idx} ON {artifacts_table} (workspace_id)"))
            else:
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {artifacts_table} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            workspace_id INTEGER NOT NULL,
                            name VARCHAR(128) NOT NULL,
                            description TEXT,
                            owner_id INTEGER,
                            created_by INTEGER,
                            created_at TIMESTAMP NOT NULL,
                            updated_at TIMESTAMP NOT NULL
                        )
                        """
                    )
                )
    insp = inspect(engine)
    if not insp.has_table(versions_table):
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {versions_table} (
                            id INT AUTO_INCREMENT PRIMARY KEY,
                            artifact_id INT NOT NULL,
                            version INT NOT NULL,
                            file_name VARCHAR(256) NOT NULL,
                            size_bytes BIGINT NULL,
                            sha256 VARCHAR(64) NULL,
                            storage_key VARCHAR(512) NULL,
                            change_note TEXT NULL,
                            status VARCHAR(16) NOT NULL DEFAULT 'active',
                            uploaded_by INT NULL,
                            uploaded_at DATETIME NOT NULL,
                            UNIQUE KEY {uq_ver} (artifact_id, version),
                            INDEX {ver_idx} (artifact_id),
                            CONSTRAINT {fk_art} FOREIGN KEY (artifact_id)
                                REFERENCES {artifacts_table}(id) ON DELETE CASCADE
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                        """
                    )
                )
            elif engine.dialect.name == "postgresql":
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {versions_table} (
                            id SERIAL PRIMARY KEY,
                            artifact_id INTEGER NOT NULL REFERENCES {artifacts_table}(id) ON DELETE CASCADE,
                            version INTEGER NOT NULL,
                            file_name VARCHAR(256) NOT NULL,
                            size_bytes BIGINT,
                            sha256 VARCHAR(64),
                            storage_key VARCHAR(512),
                            change_note TEXT,
                            status VARCHAR(16) NOT NULL DEFAULT 'active',
                            uploaded_by INTEGER,
                            uploaded_at TIMESTAMP NOT NULL
                        )
                        """
                    )
                )
                conn.execute(text(f"CREATE UNIQUE INDEX {uq_ver} ON {versions_table} (artifact_id, version)"))
                conn.execute(text(f"CREATE INDEX {ver_idx} ON {versions_table} (artifact_id)"))
            else:
                conn.execute(
                    text(
                        f"""
                        CREATE TABLE {versions_table} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            artifact_id INTEGER NOT NULL,
                            version INTEGER NOT NULL,
                            file_name VARCHAR(256) NOT NULL,
                            size_bytes INTEGER,
                            sha256 VARCHAR(64),
                            storage_key VARCHAR(512),
                            change_note TEXT,
                            status VARCHAR(16) NOT NULL DEFAULT 'active',
                            uploaded_by INTEGER,
                            uploaded_at TIMESTAMP NOT NULL,
                            UNIQUE (artifact_id, version)
                        )
                        """
                    )
                )


def migrate_dw_streaming_resource_libraries(engine: Engine) -> None:
    """连接器 / 依赖文件制品库 + 作业绑定列。"""
    _create_streaming_artifact_tables(
        engine,
        artifacts_table="dw_streaming_connector_artifacts",
        versions_table="dw_streaming_connector_versions",
        art_idx="idx_sca_ws",
        ver_idx="idx_scv_art",
        fk_ws="fk_sca_ws",
        fk_art="fk_scv_art",
        uq_ver="uq_scv_art_ver",
    )
    _create_streaming_artifact_tables(
        engine,
        artifacts_table="dw_streaming_file_artifacts",
        versions_table="dw_streaming_file_versions",
        art_idx="idx_sfa_ws",
        ver_idx="idx_sfv_art",
        fk_ws="fk_sfa_ws",
        fk_art="fk_sfv_art",
        uq_ver="uq_sfv_art_ver",
    )
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    with engine.begin() as conn:
        if "connector_version_ids" not in cols:
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN connector_version_ids TEXT"))
        if "dependency_file_version_ids" not in cols:
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN dependency_file_version_ids TEXT"))


def migrate_dw_streaming_job_history(engine: Engine) -> None:
    """实时作业脚本 / JAR 参数版本表（对齐数据开发 dw_node_history）。"""
    insp = inspect(engine)
    if insp.has_table("dw_streaming_job_history"):
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_streaming_job_history (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        job_id INT NOT NULL,
                        job_type VARCHAR(16) NOT NULL,
                        script_content TEXT NULL,
                        main_class VARCHAR(256) NULL,
                        program_args TEXT NULL,
                        parallelism INT NULL,
                        saved_at DATETIME NOT NULL,
                        saved_by INT NULL,
                        INDEX idx_sjh_job_saved (job_id, saved_at),
                        CONSTRAINT fk_sjh_job FOREIGN KEY (job_id)
                            REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
            )
        elif engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_streaming_job_history (
                        id SERIAL PRIMARY KEY,
                        job_id INTEGER NOT NULL REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE,
                        job_type VARCHAR(16) NOT NULL,
                        script_content TEXT,
                        main_class VARCHAR(256),
                        program_args TEXT,
                        parallelism INTEGER,
                        saved_at TIMESTAMP NOT NULL,
                        saved_by INTEGER
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX idx_sjh_job_saved ON dw_streaming_job_history (job_id, saved_at)"))
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_streaming_job_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id INTEGER NOT NULL,
                        job_type VARCHAR(16) NOT NULL,
                        script_content TEXT,
                        main_class VARCHAR(256),
                        program_args TEXT,
                        parallelism INTEGER,
                        saved_at TIMESTAMP NOT NULL,
                        saved_by INTEGER,
                        FOREIGN KEY (job_id) REFERENCES dw_streaming_jobs(id) ON DELETE CASCADE
                    )
                    """
                )
            )


def migrate_dw_flink_session_profiles(engine: Engine) -> None:
    """工作空间下多套 Flink Session / Gateway 配置（对标数据源多行）。"""
    insp = inspect(engine)
    if insp.has_table("dw_flink_session_profiles"):
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_flink_session_profiles (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id INT NOT NULL,
                        name VARCHAR(128) NOT NULL,
                        flink_url VARCHAR(512) NULL,
                        flink_sql_gateway_url VARCHAR(512) NULL,
                        flink_gateway_jobmanager_rest_url VARCHAR(512) NULL,
                        flink_ui_url VARCHAR(512) NULL,
                        flink_k8s_application_image VARCHAR(512) NULL,
                        flink_k8s_namespace VARCHAR(256) NULL,
                        flink_k8s_application_jm_rest_template VARCHAR(1024) NULL,
                        flink_k8s_cluster_domain VARCHAR(256) NULL,
                        flink_k8s_apiserver_fallback_url VARCHAR(512) NULL,
                        flink_k8s_jm_rpc_host VARCHAR(512) NULL,
                        flink_k8s_sql_gateway_rest_host VARCHAR(512) NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        created_by INT NULL,
                        INDEX idx_fsp_workspace (workspace_id),
                        CONSTRAINT fk_fsp_workspace FOREIGN KEY (workspace_id) REFERENCES dw_workspaces(id),
                        CONSTRAINT fk_fsp_user FOREIGN KEY (created_by) REFERENCES dw_users(id)
                    )
                    """
                )
            )
        elif engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_flink_session_profiles (
                        id SERIAL PRIMARY KEY,
                        workspace_id INTEGER NOT NULL REFERENCES dw_workspaces(id),
                        name VARCHAR(128) NOT NULL,
                        flink_url VARCHAR(512),
                        flink_sql_gateway_url VARCHAR(512),
                        flink_gateway_jobmanager_rest_url VARCHAR(512),
                        flink_ui_url VARCHAR(512),
                        flink_k8s_application_image VARCHAR(512),
                        flink_k8s_namespace VARCHAR(256),
                        flink_k8s_application_jm_rest_template VARCHAR(1024),
                        flink_k8s_cluster_domain VARCHAR(256),
                        flink_k8s_apiserver_fallback_url VARCHAR(512),
                        flink_k8s_jm_rpc_host VARCHAR(512),
                        flink_k8s_sql_gateway_rest_host VARCHAR(512),
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        created_by INTEGER REFERENCES dw_users(id)
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX idx_fsp_workspace ON dw_flink_session_profiles (workspace_id)"))
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_flink_session_profiles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id INTEGER NOT NULL,
                        name VARCHAR(128) NOT NULL,
                        flink_url VARCHAR(512),
                        flink_sql_gateway_url VARCHAR(512),
                        flink_gateway_jobmanager_rest_url VARCHAR(512),
                        flink_ui_url VARCHAR(512),
                        flink_k8s_application_image VARCHAR(512),
                        flink_k8s_namespace VARCHAR(256),
                        flink_k8s_application_jm_rest_template VARCHAR(1024),
                        flink_k8s_cluster_domain VARCHAR(256),
                        flink_k8s_apiserver_fallback_url VARCHAR(512),
                        flink_k8s_jm_rpc_host VARCHAR(512),
                        flink_k8s_sql_gateway_rest_host VARCHAR(512),
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        created_by INTEGER,
                        FOREIGN KEY (workspace_id) REFERENCES dw_workspaces(id),
                        FOREIGN KEY (created_by) REFERENCES dw_users(id)
                    )
                    """
                )
            )


def migrate_dw_streaming_jobs_flink_session_profile(engine: Engine) -> None:
    """实时作业可选绑定 Flink Session 配置（空=沿用平台默认集成）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_streaming_jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_streaming_jobs")}
    if "flink_session_profile_id" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN flink_session_profile_id INT NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_streaming_jobs ADD COLUMN flink_session_profile_id INTEGER"))


def migrate_dw_sync_tasks_enhance(engine: Engine) -> None:
    """数据集成：任务描述、最近状态、运行记录触发方式与耗时。"""
    insp = inspect(engine)
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if insp.has_table("dw_sync_tasks"):
            cols = {c["name"] for c in insp.get_columns("dw_sync_tasks")}
            if "description" not in cols:
                conn.execute(text("ALTER TABLE dw_sync_tasks ADD COLUMN description TEXT"))
            if "last_run_status" not in cols:
                conn.execute(text("ALTER TABLE dw_sync_tasks ADD COLUMN last_run_status VARCHAR(32)"))
            if "updated_at" not in cols:
                if dialect == "mysql":
                    conn.execute(
                        text(
                            "ALTER TABLE dw_sync_tasks ADD COLUMN updated_at DATETIME "
                            "DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                        )
                    )
                else:
                    conn.execute(
                        text("ALTER TABLE dw_sync_tasks ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                    )
        if insp.has_table("dw_sync_records"):
            cols = {c["name"] for c in insp.get_columns("dw_sync_records")}
            if "trigger_type" not in cols:
                conn.execute(text("ALTER TABLE dw_sync_records ADD COLUMN trigger_type VARCHAR(32) DEFAULT 'manual'"))
            if "duration_ms" not in cols:
                conn.execute(text("ALTER TABLE dw_sync_records ADD COLUMN duration_ms INTEGER"))


def migrate_workspace_space_settings(engine: Engine) -> None:
    """工作空间：默认/数仓数据源；按空间的 Dolphin/Flink 集成表。"""
    insp = inspect(engine)
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if insp.has_table("dw_workspaces"):
            cols = {c["name"] for c in insp.get_columns("dw_workspaces")}
            if "default_datasource_id" not in cols:
                conn.execute(text("ALTER TABLE dw_workspaces ADD COLUMN default_datasource_id INTEGER"))
            if "warehouse_datasource_id" not in cols:
                conn.execute(text("ALTER TABLE dw_workspaces ADD COLUMN warehouse_datasource_id INTEGER"))

        table_exists = insp.has_table("dw_workspace_platform_integration")
        if not table_exists and dialect == "mysql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_workspace_platform_integration (
                        workspace_id INT NOT NULL PRIMARY KEY,
                        ds_enabled TINYINT(1) NULL,
                        ds_url VARCHAR(512) NULL,
                        ds_ui_url VARCHAR(512) NULL,
                        ds_token TEXT NULL,
                        ds_project_name VARCHAR(128) NULL,
                        flink_url VARCHAR(512) NULL,
                        flink_sql_gateway_url VARCHAR(512) NULL,
                        flink_gateway_jobmanager_rest_url VARCHAR(512) NULL,
                        flink_ui_url VARCHAR(512) NULL,
                        flink_k8s_application_image VARCHAR(512) NULL,
                        flink_k8s_namespace VARCHAR(256) NULL,
                        flink_k8s_application_jm_rest_template VARCHAR(1024) NULL,
                        flink_k8s_cluster_domain VARCHAR(256) NULL,
                        flink_k8s_apiserver_fallback_url VARCHAR(512) NULL,
                        flink_k8s_jm_rpc_host VARCHAR(512) NULL,
                        flink_k8s_sql_gateway_rest_host VARCHAR(512) NULL,
                        updated_at DATETIME NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
            )
        elif not table_exists and dialect == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_workspace_platform_integration (
                        workspace_id INTEGER NOT NULL PRIMARY KEY REFERENCES dw_workspaces(id),
                        ds_enabled BOOLEAN NULL,
                        ds_url VARCHAR(512) NULL,
                        ds_ui_url VARCHAR(512) NULL,
                        ds_token TEXT NULL,
                        ds_project_name VARCHAR(128) NULL,
                        flink_url VARCHAR(512) NULL,
                        flink_sql_gateway_url VARCHAR(512) NULL,
                        flink_gateway_jobmanager_rest_url VARCHAR(512) NULL,
                        flink_ui_url VARCHAR(512) NULL,
                        flink_k8s_application_image VARCHAR(512) NULL,
                        flink_k8s_namespace VARCHAR(256) NULL,
                        flink_k8s_application_jm_rest_template VARCHAR(1024) NULL,
                        flink_k8s_cluster_domain VARCHAR(256) NULL,
                        flink_k8s_apiserver_fallback_url VARCHAR(512) NULL,
                        flink_k8s_jm_rpc_host VARCHAR(512) NULL,
                        flink_k8s_sql_gateway_rest_host VARCHAR(512) NULL,
                        updated_at TIMESTAMP NULL
                    )
                    """
                )
            )
        elif not table_exists:
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_workspace_platform_integration (
                        workspace_id INTEGER NOT NULL PRIMARY KEY,
                        ds_enabled BOOLEAN,
                        ds_url VARCHAR(512),
                        ds_ui_url VARCHAR(512),
                        ds_token TEXT,
                        ds_project_name VARCHAR(128),
                        flink_url VARCHAR(512),
                        flink_sql_gateway_url VARCHAR(512),
                        flink_gateway_jobmanager_rest_url VARCHAR(512),
                        flink_ui_url VARCHAR(512),
                        flink_k8s_application_image VARCHAR(512),
                        flink_k8s_namespace VARCHAR(256),
                        flink_k8s_application_jm_rest_template VARCHAR(1024),
                        flink_k8s_cluster_domain VARCHAR(256),
                        flink_k8s_apiserver_fallback_url VARCHAR(512),
                        flink_k8s_jm_rpc_host VARCHAR(512),
                        flink_k8s_sql_gateway_rest_host VARCHAR(512),
                        updated_at TIMESTAMP
                    )
                    """
                )
            )

    if not insp.has_table("dw_workspaces") or not insp.has_table("dw_workspace_platform_integration"):
        return
    if not insp.has_table("dw_platform_integration"):
        return
    with engine.begin() as conn:
        ws_rows = conn.execute(text("SELECT id FROM dw_workspaces")).fetchall()
        global_row = conn.execute(text("SELECT * FROM dw_platform_integration WHERE id = 1")).fetchone()
        if not global_row:
            return
        g = global_row._mapping
        for (ws_id,) in ws_rows:
            exists = conn.execute(
                text("SELECT 1 FROM dw_workspace_platform_integration WHERE workspace_id = :wid"),
                {"wid": ws_id},
            ).fetchone()
            if exists:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO dw_workspace_platform_integration (
                        workspace_id, ds_enabled, ds_url, ds_ui_url, ds_token, ds_project_name,
                        flink_url, flink_sql_gateway_url, flink_gateway_jobmanager_rest_url, flink_ui_url,
                        flink_k8s_application_image, flink_k8s_namespace,
                        flink_k8s_application_jm_rest_template, flink_k8s_cluster_domain,
                        flink_k8s_apiserver_fallback_url, flink_k8s_jm_rpc_host, flink_k8s_sql_gateway_rest_host
                    ) VALUES (
                        :workspace_id, :ds_enabled, :ds_url, :ds_ui_url, :ds_token, :ds_project_name,
                        :flink_url, :flink_sql_gateway_url, :flink_gateway_jobmanager_rest_url, :flink_ui_url,
                        :flink_k8s_application_image, :flink_k8s_namespace,
                        :flink_k8s_application_jm_rest_template, :flink_k8s_cluster_domain,
                        :flink_k8s_apiserver_fallback_url, :flink_k8s_jm_rpc_host, :flink_k8s_sql_gateway_rest_host
                    )
                    """
                ),
                {
                    "workspace_id": ws_id,
                    "ds_enabled": g.get("ds_enabled"),
                    "ds_url": g.get("ds_url"),
                    "ds_ui_url": g.get("ds_ui_url"),
                    "ds_token": g.get("ds_token"),
                    "ds_project_name": g.get("ds_project_name"),
                    "flink_url": g.get("flink_url"),
                    "flink_sql_gateway_url": g.get("flink_sql_gateway_url"),
                    "flink_gateway_jobmanager_rest_url": g.get("flink_gateway_jobmanager_rest_url"),
                    "flink_ui_url": g.get("flink_ui_url"),
                    "flink_k8s_application_image": g.get("flink_k8s_application_image"),
                    "flink_k8s_namespace": g.get("flink_k8s_namespace"),
                    "flink_k8s_application_jm_rest_template": g.get("flink_k8s_application_jm_rest_template"),
                    "flink_k8s_cluster_domain": g.get("flink_k8s_cluster_domain"),
                    "flink_k8s_apiserver_fallback_url": g.get("flink_k8s_apiserver_fallback_url"),
                    "flink_k8s_jm_rpc_host": g.get("flink_k8s_jm_rpc_host"),
                    "flink_k8s_sql_gateway_rest_host": g.get("flink_k8s_sql_gateway_rest_host"),
                },
            )


def migrate_workflow_instance_trigger_type_widen(engine: Engine) -> None:
    """工作流实例：trigger_type 加长（manual|ds:大整数 等，避免 VARCHAR(32) 截断导致无法匹配 Dolphin）。"""
    import logging

    _log = logging.getLogger(__name__)
    insp = inspect(engine)
    if not insp.has_table("dw_workflow_instances"):
        return
    if engine.dialect.name not in ("mysql", "postgresql"):
        return
    col = next((c for c in insp.get_columns("dw_workflow_instances") if c["name"] == "trigger_type"), None)
    if not col:
        return
    length = getattr(col["type"], "length", None)
    if length is not None and length >= 128:
        return
    try:
        with engine.begin() as conn:
            if engine.dialect.name == "mysql":
                conn.execute(
                    text("ALTER TABLE dw_workflow_instances MODIFY COLUMN trigger_type VARCHAR(128) DEFAULT 'manual'")
                )
            else:
                conn.execute(
                    text("ALTER TABLE dw_workflow_instances ALTER COLUMN trigger_type TYPE VARCHAR(128)")
                )
    except Exception as e:
        _log.warning("migrate_workflow_instance_trigger_type_widen: %s", e)


def migrate_workflow_instance_dolphin_command_type(engine: Engine) -> None:
    """工作流实例：Dolphin commandType 回填列（区分定时调度与手动触发）。"""
    insp = inspect(engine)
    if not insp.has_table("dw_workflow_instances"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_workflow_instances")}
    if "dolphin_command_type" in cols:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("ALTER TABLE dw_workflow_instances ADD COLUMN dolphin_command_type VARCHAR(64) NULL"))
        else:
            conn.execute(text("ALTER TABLE dw_workflow_instances ADD COLUMN dolphin_command_type VARCHAR(64)"))


def seed_permissions(db: Session) -> dict[str, Permission]:
    by_code: dict[str, Permission] = {}
    for code in P.ALL_PERMISSIONS:
        row = db.query(Permission).filter(Permission.code == code).first()
        if not row:
            row = Permission(
                code=code,
                name=P.PERMISSION_LABELS.get(code, code),
                module=P.PERMISSION_MODULES.get(code, ""),
            )
            db.add(row)
            db.flush()
        by_code[code] = row
    db.commit()
    return by_code


def seed_roles(db: Session, by_code: dict[str, Permission]) -> dict[str, Role]:
    all_perms = list(by_code.values())
    no_system = [by_code[c] for c in P.ALL_PERMISSIONS if not c.startswith("system:")]

    # 数据分析（只读）：仅探查 + 数据字典；不含数据源管理/开发/工作流/运维/系统管理
    # 探查下拉选源、字典目录筛选用 PROBE/DATAMAP 读权限走轻量列表，不授予 DATASOURCE_READ
    analyst_read_codes = [
        P.WORKSPACE_READ,
        P.GIDO_BATCH_PROBE_READ,
        P.GIDO_BATCH_DATAMAP_READ,
    ]
    read_only = [by_code[c] for c in analyst_read_codes if c in by_code]

    operator_codes = []
    for c in P.ALL_PERMISSIONS:
        if c.startswith("system:"):
            continue
        if c.endswith(":read") or c == P.WORKSPACE_READ:
            operator_codes.append(by_code[c])
        if c in (
            P.GIDO_BATCH_OPERATION_WRITE,
            P.GIDO_BATCH_INTEGRATION_RUN,
            P.GIDO_BATCH_WORKFLOW_RUN,
            P.GIDO_BATCH_STUDIO_RUN,
            P.GIDO_SERVICE_RUN,
            P.GIDO_STREAM_RUN,  # 与商业化运维一致：可部署/停止，不可改作业定义
        ):
            operator_codes.append(by_code[c])
    # 去重保序
    seen = set()
    operator_perms = []
    for p in operator_codes:
        if p.id not in seen:
            seen.add(p.id)
            operator_perms.append(p)

    workspace_steward_perms = [
        by_code[P.WORKSPACE_READ],
        by_code[P.GIDO_BATCH_DATASOURCE_READ],
        by_code[P.GIDO_BATCH_DATASOURCE_WRITE],
    ]

    specs = [
        ("super_admin", "超级管理员", "内置；全部权限（与 is_admin 等价超集）", True, all_perms),
        ("platform_admin", "平台管理员", "内置；用户/角色管理与全业务权限", True, all_perms),
        ("developer", "开发工程师", "内置；业务开发全权限（无系统管理）", True, no_system),
        ("workspace_steward", "空间管理员（数据源）", "内置；仅数据源读写 + 查看空间列表；实际可操作范围由「空间成员角色」限定在自己归属的空间", True, workspace_steward_perms),
        ("analyst", "数据分析（只读）", "内置；数据探查 + 数据字典（无数据源管理/开发/工作流/运维/系统管理）", True, read_only),
        (
            "operator",
            "运维工程师",
            "内置；各模块只读 + 批/流/服务运行与运维写；不可改 Studio/Stream 作业定义与 Serve 配置",
            True,
            operator_perms,
        ),
    ]

    out: dict[str, Role] = {}
    for code, name, desc, is_sys, perms in specs:
        row = db.query(Role).filter(Role.code == code).first()
        if not row:
            row = Role(code=code, name=name, description=desc, is_system=is_sys)
            db.add(row)
            db.flush()
        row.name = name
        row.description = desc
        row.is_system = is_sys
        row.permissions = perms
        out[code] = row
    db.commit()
    return out


def assign_default_roles(db: Session, roles: dict[str, Role]):
    dev = roles.get("developer")
    sup = roles.get("super_admin")
    for u in db.query(User).all():
        if u.role_id:
            continue
        # is_admin 可能为 NULL（历史库）；用户名为 admin 的账号按平台管理员处理
        if sup and (u.is_admin is True or u.username == "admin"):
            u.role_id = sup.id
            if u.username == "admin" and u.is_admin is not True:
                u.is_admin = True
        elif dev:
            u.role_id = dev.id
    db.commit()


def migrate_dw_data_service(engine: Engine) -> None:
    """数据服务：API、参数、消费者应用、授权、调用日志。"""
    insp = inspect(engine)
    dialect = engine.dialect.name
    tables = {
        "dw_data_apis": """
            CREATE TABLE dw_data_apis (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER NOT NULL,
                api_code VARCHAR(64) NOT NULL,
                name VARCHAR(128) NOT NULL,
                description TEXT,
                mode VARCHAR(16) NOT NULL DEFAULT 'sql',
                http_method VARCHAR(8) DEFAULT 'GET',
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                version INTEGER DEFAULT 1,
                datasource_id INTEGER,
                sql_template TEXT,
                wizard_config JSON,
                response_fields JSON,
                pagination_enabled BOOLEAN DEFAULT TRUE,
                page_size_default INTEGER DEFAULT 20,
                page_size_max INTEGER DEFAULT 1000,
                timeout_seconds INTEGER DEFAULT 30,
                cache_ttl_seconds INTEGER DEFAULT 0,
                max_rows INTEGER DEFAULT 10000,
                pending_definition JSON,
                owner_id INTEGER,
                published_at TIMESTAMP,
                published_by INTEGER,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                created_by INTEGER,
                UNIQUE(workspace_id, api_code)
            )
        """,
        "dw_data_api_params": """
            CREATE TABLE dw_data_api_params (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                name VARCHAR(64) NOT NULL,
                param_in VARCHAR(16) DEFAULT 'query',
                data_type VARCHAR(16) DEFAULT 'string',
                required BOOLEAN DEFAULT FALSE,
                default_value VARCHAR(512),
                description VARCHAR(256),
                validator_regex VARCHAR(256),
                sort_order INTEGER DEFAULT 0
            )
        """,
        "dw_consumer_apps": """
            CREATE TABLE dw_consumer_apps (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER NOT NULL,
                name VARCHAR(128) NOT NULL,
                description TEXT,
                app_key VARCHAR(32) NOT NULL,
                app_secret_hash VARCHAR(256) NOT NULL,
                ip_whitelist JSON,
                qps_limit INTEGER DEFAULT 100,
                daily_quota INTEGER DEFAULT 100000,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP,
                created_by INTEGER,
                UNIQUE(workspace_id, app_key)
            )
        """,
        "dw_consumer_app_api_grants": """
            CREATE TABLE dw_consumer_app_api_grants (
                id INTEGER PRIMARY KEY,
                app_id INTEGER NOT NULL,
                api_id INTEGER NOT NULL,
                qps_limit INTEGER,
                created_at TIMESTAMP,
                UNIQUE(app_id, api_id)
            )
        """,
        "dw_data_api_invocation_logs": """
            CREATE TABLE dw_data_api_invocation_logs (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                api_id INTEGER,
                app_id INTEGER,
                trace_id VARCHAR(64),
                http_method VARCHAR(8),
                client_ip VARCHAR(64),
                request_params JSON,
                status_code INTEGER,
                row_count INTEGER DEFAULT 0,
                latency_ms FLOAT,
                cache_hit BOOLEAN DEFAULT FALSE,
                error_message TEXT,
                created_at TIMESTAMP
            )
        """,
    }
    if dialect == "mysql":
        tables["dw_data_apis"] = tables["dw_data_apis"].replace("INTEGER PRIMARY KEY", "INT AUTO_INCREMENT PRIMARY KEY")
        tables["dw_data_api_params"] = tables["dw_data_api_params"].replace("INTEGER PRIMARY KEY", "INT AUTO_INCREMENT PRIMARY KEY")
        tables["dw_consumer_apps"] = tables["dw_consumer_apps"].replace("INTEGER PRIMARY KEY", "INT AUTO_INCREMENT PRIMARY KEY")
        tables["dw_consumer_app_api_grants"] = tables["dw_consumer_app_api_grants"].replace("INTEGER PRIMARY KEY", "INT AUTO_INCREMENT PRIMARY KEY")
        tables["dw_data_api_invocation_logs"] = tables["dw_data_api_invocation_logs"].replace("INTEGER PRIMARY KEY", "INT AUTO_INCREMENT PRIMARY KEY")
        tables["dw_data_apis"] = tables["dw_data_apis"].replace("JSON", "JSON")
    with engine.begin() as conn:
        for name, ddl in tables.items():
            if not insp.has_table(name):
                conn.execute(text(ddl))
        # 存量库补列：线上不中断的待发布定义
        if insp.has_table("dw_data_apis"):
            cols = {c["name"] for c in insp.get_columns("dw_data_apis")}
            if "pending_definition" not in cols:
                conn.execute(text("ALTER TABLE dw_data_apis ADD COLUMN pending_definition JSON"))


def migrate_dw_workspace_variables(engine: Engine) -> None:
    """工作空间全局变量（Batch/Stream/Serve 共用 ${var_key}）。"""
    insp = inspect(engine)
    if insp.has_table("dw_workspace_variables"):
        return
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_workspace_variables (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        workspace_id INT NOT NULL,
                        var_key VARCHAR(128) NOT NULL,
                        var_value TEXT NULL,
                        is_secret TINYINT(1) NOT NULL DEFAULT 0,
                        scope VARCHAR(32) NOT NULL DEFAULT 'all',
                        description TEXT NULL,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        created_by INT NULL,
                        INDEX idx_wv_workspace (workspace_id),
                        UNIQUE KEY uq_wv_ws_key (workspace_id, var_key),
                        CONSTRAINT fk_wv_workspace FOREIGN KEY (workspace_id) REFERENCES dw_workspaces(id),
                        CONSTRAINT fk_wv_user FOREIGN KEY (created_by) REFERENCES dw_users(id)
                    )
                    """
                )
            )
        elif engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_workspace_variables (
                        id SERIAL PRIMARY KEY,
                        workspace_id INTEGER NOT NULL REFERENCES dw_workspaces(id),
                        var_key VARCHAR(128) NOT NULL,
                        var_value TEXT,
                        is_secret BOOLEAN NOT NULL DEFAULT FALSE,
                        scope VARCHAR(32) NOT NULL DEFAULT 'all',
                        description TEXT,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        created_by INTEGER REFERENCES dw_users(id),
                        UNIQUE (workspace_id, var_key)
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX idx_wv_workspace ON dw_workspace_variables (workspace_id)"))
        else:
            conn.execute(
                text(
                    """
                    CREATE TABLE dw_workspace_variables (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id INTEGER NOT NULL,
                        var_key VARCHAR(128) NOT NULL,
                        var_value TEXT,
                        is_secret BOOLEAN NOT NULL DEFAULT 0,
                        scope VARCHAR(32) NOT NULL DEFAULT 'all',
                        description TEXT,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        created_by INTEGER,
                        FOREIGN KEY (workspace_id) REFERENCES dw_workspaces(id),
                        FOREIGN KEY (created_by) REFERENCES dw_users(id),
                        UNIQUE (workspace_id, var_key)
                    )
                    """
                )
            )
            conn.execute(text("CREATE INDEX idx_wv_workspace ON dw_workspace_variables (workspace_id)"))


def run_rbac_bootstrap(db: Session):
    by_code = seed_permissions(db)
    roles = seed_roles(db, by_code)
    assign_default_roles(db, roles)
    from app.services.workspace_default import backfill_all_users_default_workspace

    backfill_all_users_default_workspace(db)
    return roles

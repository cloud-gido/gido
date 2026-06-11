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
    """TaskNode：同目录内展示顺序（手动拖拽；编辑脚本不自动改变）。"""
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
        # 按创建时间回填，保证升级后顺序稳定
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    UPDATE dw_task_nodes AS t SET sort_order = r.rn * 10
                    FROM (
                      SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY workspace_id, folder_id
                        ORDER BY created_at NULLS LAST, id
                      ) AS rn
                      FROM dw_task_nodes
                    ) AS r
                    WHERE t.id = r.id AND (t.sort_order IS NULL OR t.sort_order = 0)
                    """
                )
            )
        elif engine.dialect.name == "mysql":
            conn.execute(
                text(
                    """
                    UPDATE dw_task_nodes t
                    JOIN (
                      SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY workspace_id, folder_id
                        ORDER BY created_at, id
                      ) AS rn
                      FROM dw_task_nodes
                    ) r ON t.id = r.id
                    SET t.sort_order = r.rn * 10
                    WHERE t.sort_order IS NULL OR t.sort_order = 0
                    """
                )
            )
        else:
            conn.execute(
                text(
                    "UPDATE dw_task_nodes SET sort_order = id * 10 WHERE sort_order IS NULL OR sort_order = 0"
                )
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
                        program_args VARCHAR(512) NULL,
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
                        program_args VARCHAR(512),
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
                        program_args VARCHAR(512),
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

    # 数据分析（只读）：仅探查 + 数据字典 + 数据源列表；不含开发/工作流/运维/系统管理
    analyst_read_codes = [
        P.WORKSPACE_READ,
        P.GIDO_BATCH_PROBE_READ,
        P.GIDO_BATCH_DATAMAP_READ,
        P.GIDO_BATCH_DATASOURCE_READ,
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
        ("analyst", "数据分析（只读）", "内置；数据探查 + 数据字典 + 数据源查看（无开发/工作流/运维/系统管理）", True, read_only),
        ("operator", "运维工程师", "内置；只读 + 运维写 + 部分运行", True, operator_perms),
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

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Enum, JSON, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.database import Base
import enum


# ==================== 工作空间 ====================

class User(Base):
    __tablename__ = "dw_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False)
    email = Column(String(128), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    full_name = Column(String(64))
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    role_id = Column(Integer, ForeignKey("dw_roles.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    system_role = relationship("Role", back_populates="users", foreign_keys=[role_id])


class Workspace(Base):
    __tablename__ = "dw_workspaces"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(Text)
    owner_id = Column(Integer, ForeignKey("dw_users.id"))
    timezone = Column(String(64), default="Asia/Shanghai")  # 工作空间时区
    # 空间级默认数据源：开发/探查/SQL 节点；warehouse 未设时集成目标也用它
    default_datasource_id = Column(Integer, ForeignKey("dw_datasources.id"), nullable=True)
    warehouse_datasource_id = Column(Integer, ForeignKey("dw_datasources.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    members = relationship("WorkspaceMember", back_populates="workspace")


class WorkspaceMember(Base):
    __tablename__ = "dw_workspace_members"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    user_id = Column(Integer, ForeignKey("dw_users.id"))
    role = Column(String(32), default="developer")  # admin/developer/viewer
    workspace = relationship("Workspace", back_populates="members")


# ==================== 审计日志 ====================

class AuditLog(Base):
    __tablename__ = "dw_audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("dw_users.id"))
    action = Column(String(64), nullable=False)   # create/update/delete/run/publish
    resource_type = Column(String(64))             # node/workflow/datasource/rule
    resource_id = Column(Integer)
    resource_name = Column(String(256))
    detail = Column(JSON)
    ip_address = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)


# ==================== 数据源 ====================

class DataSource(Base):
    __tablename__ = "dw_datasources"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    name = Column(String(64), nullable=False)
    ds_type = Column(String(32), nullable=False)  # mysql/postgresql/doris/hive/kafka/oss
    host = Column(String(256))
    port = Column(Integer)
    database = Column(String(64))
    username = Column(String(64))
    password = Column(String(256))
    extra_config = Column(JSON)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"))


# ==================== 数据开发 ====================

class NodeType(str, enum.Enum):
    SQL = "SQL"
    PYTHON = "PYTHON"
    SHELL = "SHELL"
    SYNC = "SYNC"
    VIRTUAL = "VIRTUAL"


class TaskNode(Base):
    __tablename__ = "dw_task_nodes"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    name = Column(String(128), nullable=False)
    node_type = Column(String(32), nullable=False)
    script_content = Column(Text)
    datasource_id = Column(Integer, ForeignKey("dw_datasources.id"), nullable=True)
    params = Column(JSON)                          # 变量参数，如 {bizdate, env}
    folder_id = Column(Integer, ForeignKey("dw_node_folders.id"), nullable=True)
    sort_order = Column(Integer, default=0)  # 同目录内手动排序，编辑脚本不改变
    timeout_seconds = Column(Integer, default=3600)
    retry_times = Column(Integer, default=0)
    is_published = Column(Boolean, default=False)  # 是否已提交到工作流
    owner_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)  # 脚本负责人（默认同创建人）
    is_locked = Column(Boolean, default=False)  # 提交发布后锁定，需显式解锁才可改脚本
    # 协作编辑锁（GIDO：当前占用编辑会话的人；与 is_locked 发布锁独立）
    edit_lock_user_id = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    edit_lock_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"))


class NodeFolder(Base):
    """节点文件夹/分组"""
    __tablename__ = "dw_node_folders"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    name = Column(String(128), nullable=False)
    parent_id = Column(Integer, ForeignKey("dw_node_folders.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class NodeDependency(Base):
    __tablename__ = "dw_node_dependencies"
    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, ForeignKey("dw_task_nodes.id"))
    depends_on_id = Column(Integer, ForeignKey("dw_task_nodes.id"))


class NodeHistory(Base):
    __tablename__ = "dw_node_history"
    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, ForeignKey("dw_task_nodes.id"))
    script_content = Column(Text)
    saved_at = Column(DateTime, default=datetime.utcnow)
    saved_by = Column(Integer, ForeignKey("dw_users.id"))


# ==================== 工作流 ====================

class Workflow(Base):
    __tablename__ = "dw_workflows"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    name = Column(String(128), nullable=False)
    description = Column(Text)
    dag_config = Column(JSON)  # 节点和边的配置
    schedule_type = Column(String(32), default="manual")
    cron_expression = Column(String(64))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"))
    updated_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    instances = relationship("WorkflowInstance", back_populates="workflow")


class WorkflowInstance(Base):
    __tablename__ = "dw_workflow_instances"
    id = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("dw_workflows.id"))
    status = Column(String(32), default="pending")  # pending/running/success/failed/killed
    trigger_type = Column(String(128), default="manual")  # manual|ds:{id} / schedule|ds:{id} 等，需容纳 Dolphin 实例 ID
    # Dolphin 流程实例详情中的 commandType（如 SCHEDULER）；用于运维展示，与 trigger_type 前缀解耦
    dolphin_command_type = Column(String(64), nullable=True)
    business_date = Column(String(32))
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    submitted_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    workflow = relationship("Workflow", back_populates="instances")
    node_instances = relationship("NodeInstance", back_populates="workflow_instance")


class NodeInstance(Base):
    __tablename__ = "dw_node_instances"
    id = Column(Integer, primary_key=True, index=True)
    workflow_instance_id = Column(Integer, ForeignKey("dw_workflow_instances.id"))
    node_id = Column(Integer, ForeignKey("dw_task_nodes.id"))
    status = Column(String(32), default="pending")
    log_content = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    retry_count = Column(Integer, default=0)
    workflow_instance = relationship("WorkflowInstance", back_populates="node_instances")


# ==================== 数据集成 ====================

class SyncTask(Base):
    __tablename__ = "dw_sync_tasks"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    name = Column(String(128), nullable=False)
    description = Column(Text)
    src_datasource_id = Column(Integer, ForeignKey("dw_datasources.id"))
    dst_datasource_id = Column(Integer, ForeignKey("dw_datasources.id"))
    src_table = Column(String(256))
    dst_table = Column(String(256))
    sync_mode = Column(String(32), default="full")  # full / incremental
    sync_config = Column(JSON)   # field_mappings, where_clause, incremental_*, batch_size, pre_sql, post_sql
    schedule_cron = Column(String(64))
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime)
    last_run_status = Column(String(32))  # running / success / failed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"))


class SyncRecord(Base):
    __tablename__ = "dw_sync_records"
    id = Column(Integer, primary_key=True, index=True)
    sync_task_id = Column(Integer, ForeignKey("dw_sync_tasks.id"))
    status = Column(String(32))  # running/success/failed
    trigger_type = Column(String(32), default="manual")  # manual / schedule
    rows_read = Column(Integer, default=0)
    rows_written = Column(Integer, default=0)
    error_msg = Column(Text)
    duration_ms = Column(Integer)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


# ==================== 数据地图 ====================

class MetaTable(Base):
    __tablename__ = "dw_meta_tables"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    datasource_id = Column(Integer, ForeignKey("dw_datasources.id"))
    db_name = Column(String(128))
    table_name = Column(String(256), nullable=False)
    table_comment = Column(Text)
    table_type = Column(String(32), default="table")  # table/view
    row_count = Column(Integer)
    size_bytes = Column(Integer)
    tags = Column(JSON)
    owner = Column(String(64))
    last_updated = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    columns = relationship("MetaColumn", back_populates="table")


class MetaColumn(Base):
    __tablename__ = "dw_meta_columns"
    id = Column(Integer, primary_key=True, index=True)
    table_id = Column(Integer, ForeignKey("dw_meta_tables.id"))
    column_name = Column(String(256), nullable=False)
    column_type = Column(String(64))
    column_comment = Column(Text)
    is_nullable = Column(Boolean, default=True)
    is_primary_key = Column(Boolean, default=False)
    ordinal_position = Column(Integer)
    table = relationship("MetaTable", back_populates="columns")


class Lineage(Base):
    __tablename__ = "dw_lineage"
    id = Column(Integer, primary_key=True, index=True)
    src_table_id = Column(Integer, ForeignKey("dw_meta_tables.id"))
    dst_table_id = Column(Integer, ForeignKey("dw_meta_tables.id"))
    task_node_id = Column(Integer, ForeignKey("dw_task_nodes.id"), nullable=True)
    lineage_type = Column(String(32), default="table")  # table/column
    created_at = Column(DateTime, default=datetime.utcnow)


# ==================== Flink Session 配置（按工作空间多套，对标数据源多行） ====================


class FlinkSessionProfile(Base):
    """
    工作空间下多套 Flink Session / Gateway 地址；字段语义同 PlatformIntegration 的 Flink 列：
    某列为 NULL 表示该项仍沿用「环境变量 + 平台集成单行」合并后的值。
    """

    __tablename__ = "dw_flink_session_profiles"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    flink_url = Column(String(512), nullable=True)
    flink_sql_gateway_url = Column(String(512), nullable=True)
    flink_gateway_jobmanager_rest_url = Column(String(512), nullable=True)
    flink_ui_url = Column(String(512), nullable=True)
    flink_k8s_application_image = Column(String(512), nullable=True)
    flink_k8s_namespace = Column(String(256), nullable=True)
    flink_k8s_application_jm_rest_template = Column(String(1024), nullable=True)
    flink_k8s_cluster_domain = Column(String(256), nullable=True)
    flink_k8s_apiserver_fallback_url = Column(String(512), nullable=True)
    flink_k8s_jm_rpc_host = Column(String(512), nullable=True)
    flink_k8s_sql_gateway_rest_host = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)


# ==================== 工作空间平台集成（按空间区分测试/生产 DS、Flink） ====================


class WorkspacePlatformIntegration(Base):
    """每个工作空间一行；各字段 NULL 表示该项沿用全局平台集成或环境变量。"""

    __tablename__ = "dw_workspace_platform_integration"
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), primary_key=True)
    ds_enabled = Column(Boolean, nullable=True)
    ds_url = Column(String(512), nullable=True)
    ds_ui_url = Column(String(512), nullable=True)
    ds_token = Column(Text, nullable=True)
    ds_project_name = Column(String(128), nullable=True)
    flink_url = Column(String(512), nullable=True)
    flink_sql_gateway_url = Column(String(512), nullable=True)
    flink_gateway_jobmanager_rest_url = Column(String(512), nullable=True)
    flink_ui_url = Column(String(512), nullable=True)
    flink_k8s_application_image = Column(String(512), nullable=True)
    flink_k8s_namespace = Column(String(256), nullable=True)
    flink_k8s_application_jm_rest_template = Column(String(1024), nullable=True)
    flink_k8s_cluster_domain = Column(String(256), nullable=True)
    flink_k8s_apiserver_fallback_url = Column(String(512), nullable=True)
    flink_k8s_jm_rpc_host = Column(String(512), nullable=True)
    flink_k8s_sql_gateway_rest_host = Column(String(512), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== 平台集成（全局回退，id=1；新部署建议用工作空间级配置） ====================


class PlatformIntegration(Base):
    """单行 id=1；各字段为 NULL 表示该项仍使用环境变量 / .env 默认值。"""

    __tablename__ = "dw_platform_integration"
    id = Column(Integer, primary_key=True, default=1)
    ds_enabled = Column(Boolean, nullable=True)
    ds_url = Column(String(512), nullable=True)
    ds_ui_url = Column(String(512), nullable=True)
    ds_token = Column(Text, nullable=True)
    ds_project_name = Column(String(128), nullable=True)
    # Flink：NULL 表示该项沿用环境变量 FLINK_*（可插拔对接集群）
    flink_url = Column(String(512), nullable=True)
    flink_sql_gateway_url = Column(String(512), nullable=True)
    flink_gateway_jobmanager_rest_url = Column(String(512), nullable=True)
    flink_ui_url = Column(String(512), nullable=True)
    # K8s Application：可插拔覆盖环境变量 FLINK_K8S_*（NULL 表示该项仍用环境变量）
    flink_k8s_application_image = Column(String(512), nullable=True)
    flink_k8s_namespace = Column(String(256), nullable=True)
    flink_k8s_application_jm_rest_template = Column(String(1024), nullable=True)
    # 集群内 SQL Gateway / Application：与 k8s 清单对齐的可插拔项（NULL=该项沿用 FLINK_K8S_* 环境变量）
    flink_k8s_cluster_domain = Column(String(256), nullable=True)
    flink_k8s_apiserver_fallback_url = Column(String(512), nullable=True)
    flink_k8s_jm_rpc_host = Column(String(512), nullable=True)
    flink_k8s_sql_gateway_rest_host = Column(String(512), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ==================== 数据质量 ====================

class QualityRule(Base):
    __tablename__ = "dw_quality_rules"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"))
    table_id = Column(Integer, ForeignKey("dw_meta_tables.id"))
    rule_name = Column(String(128), nullable=False)
    rule_type = Column(String(32))  # completeness/uniqueness/accuracy/timeliness/custom_sql/…
    rule_config = Column(JSON)
    threshold = Column(String(32))
    is_active = Column(Boolean, default=True)
    # 与 Dolphin 质量规则联动：存 DS 侧任务/规则标识或原始定义 JSON，供编排或对照
    dolphin_refs = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"))
    check_records = relationship("QualityCheckRecord", back_populates="rule")


class QualityCheckRecord(Base):
    __tablename__ = "dw_quality_check_records"
    id = Column(Integer, primary_key=True, index=True)
    rule_id = Column(Integer, ForeignKey("dw_quality_rules.id"))
    status = Column(String(32))  # pass/fail/warning
    score = Column(Integer)
    detail = Column(JSON)
    checked_at = Column(DateTime, default=datetime.utcnow)
    rule = relationship("QualityRule", back_populates="check_records")


# ==================== 发布审批 ====================


class PublishApproval(Base):
    """开发提交 → 空间/平台管理员审批 → 发布到生产（Dolphin / 脚本锁定）。"""

    __tablename__ = "dw_publish_approvals"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    resource_type = Column(String(32), nullable=False)  # workflow | studio_node
    resource_id = Column(Integer, nullable=False, index=True)
    resource_name = Column(String(256))
    action = Column(String(64), nullable=False)  # publish_to_ds | publish_node
    status = Column(String(32), default="pending", index=True)  # pending/approved/rejected/cancelled
    submit_note = Column(Text, nullable=True)
    review_note = Column(Text, nullable=True)
    submitted_by = Column(Integer, ForeignKey("dw_users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)
    submitted_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)


class WorkspaceVariable(Base):
    """工作空间全局变量：SQL / Flink 脚本中引用 ${var_key}，Batch/Stream/Serve 共用。"""
    __tablename__ = "dw_workspace_variables"
    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, ForeignKey("dw_workspaces.id"), nullable=False, index=True)
    var_key = Column(String(128), nullable=False)
    var_value = Column(Text, nullable=True)
    is_secret = Column(Boolean, default=False, nullable=False)
    scope = Column(String(32), default="all", nullable=False)  # all | batch | stream | serve
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(Integer, ForeignKey("dw_users.id"), nullable=True)

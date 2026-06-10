# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""初始化管理员账号和默认工作空间"""
import os
import sys

sys.path.append(".")
from app.core.database import SessionLocal, Base, engine
from app.core.security import get_password_hash
from app.models.workspace import User, Workspace, WorkspaceMember
from app.models import rbac_models  # noqa: F401
import app.api.streaming  # noqa: F401 — 注册 dw_streaming_jobs / dw_streaming_job_history 于 Base.metadata（须先于 create_all）
import app.models.data_service  # noqa: F401
from app.services.rbac_seed import (
    migrate_schema,
    migrate_dw_streaming_jobs,
    migrate_dw_streaming_job_history,
    migrate_dw_streaming_jobs_streaming_properties,
    migrate_dw_streaming_job_history_streaming_properties,
    migrate_dw_streaming_jobs_flink_submit_mode,
    migrate_dw_streaming_jobs_flink_jar_operator,
    migrate_dw_streaming_jobs_submit_audit_and_history_submit_mode,
    migrate_dw_flink_session_profiles,
    migrate_dw_streaming_jobs_flink_session_profile,
    migrate_dw_sync_tasks_enhance,
    migrate_dw_data_service,
    migrate_workspace_space_settings,
    migrate_workflow_instance_trigger_type_widen,
    migrate_workflow_instance_dolphin_command_type,
    migrate_dw_task_nodes_owner_lock,
    migrate_dw_task_nodes_edit_lock,
    migrate_dw_task_nodes_sort_order,
    migrate_dw_workflow_updated_by,
    migrate_dw_workflow_instance_submitted_by,
    migrate_dw_quality_dolphin_refs,
    migrate_default_workspace_to_infras,
    migrate_workspace_owner_members,
    migrate_platform_integration,
    migrate_platform_integration_flink,
    run_rbac_bootstrap,
)

Base.metadata.create_all(bind=engine)
migrate_schema(engine)
migrate_dw_task_nodes_owner_lock(engine)
migrate_dw_task_nodes_edit_lock(engine)
migrate_dw_task_nodes_sort_order(engine)
migrate_dw_workflow_updated_by(engine)
migrate_dw_workflow_instance_submitted_by(engine)
migrate_dw_quality_dolphin_refs(engine)
migrate_dw_streaming_jobs(engine)
migrate_dw_streaming_job_history(engine)
migrate_dw_streaming_jobs_streaming_properties(engine)
migrate_dw_streaming_job_history_streaming_properties(engine)
migrate_dw_streaming_jobs_flink_submit_mode(engine)
migrate_dw_streaming_jobs_flink_jar_operator(engine)
migrate_dw_streaming_jobs_submit_audit_and_history_submit_mode(engine)
migrate_dw_flink_session_profiles(engine)
migrate_dw_streaming_jobs_flink_session_profile(engine)
migrate_dw_sync_tasks_enhance(engine)
migrate_dw_data_service(engine)
migrate_workspace_space_settings(engine)
migrate_workflow_instance_trigger_type_widen(engine)
migrate_workflow_instance_dolphin_command_type(engine)
migrate_default_workspace_to_infras(engine)
migrate_workspace_owner_members(engine)
migrate_platform_integration(engine)
migrate_platform_integration_flink(engine)

db = SessionLocal()
run_rbac_bootstrap(db)

# 创建管理员
admin = db.query(User).filter(User.username == "admin").first()
if not admin:
    admin = User(
        username="admin",
        email="admin@gido.com",
        full_name="管理员",
        hashed_password=get_password_hash("admin123"),
        is_admin=True
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    print("✅ 管理员账号创建成功: admin / admin123")
else:
    print("ℹ️  管理员账号已存在")

# Docker/本地排障：若设置 GIDO_BOOTSTRAP_ADMIN_PASSWORD，每次 init 都会把 admin 密码同步为该值（生产环境请勿设置）
_bootstrap = (os.environ.get("GIDO_BOOTSTRAP_ADMIN_PASSWORD") or "").strip()
if _bootstrap:
    admin = db.query(User).filter(User.username == "admin").first()
    if admin:
        admin.hashed_password = get_password_hash(_bootstrap)
        admin.is_active = True
        db.commit()
        print("✅ 已按 GIDO_BOOTSTRAP_ADMIN_PASSWORD 同步管理员 admin 密码（生产环境请取消该环境变量）")

# 创建默认工作空间 infras（与 dw_workspaces.name 唯一约束绑定）
ws = db.query(Workspace).filter(Workspace.name.in_(["infras", "默认工作空间"])).first()
if not ws:
    ws = Workspace(name="infras", description="系统默认工作空间", owner_id=admin.id)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    db.commit()
    print(f"✅ 默认工作空间 infras 创建成功 (id={ws.id})")
elif ws.name == "默认工作空间":
    ws.name = "infras"
    if not ws.description:
        ws.description = "系统默认工作空间"
    db.commit()
    print(f"✅ 已将「默认工作空间」重命名为 infras (id={ws.id})")
else:
    print("ℹ️  默认工作空间 infras 已存在")

run_rbac_bootstrap(db)

db.close()
print("\n🎉 初始化完成！访问 http://localhost:8001/docs")

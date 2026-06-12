# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
import logging
import sys
from contextlib import asynccontextmanager

# 使 app.services.* 的 INFO/WARNING 出现在 docker logs（不仅 uvicorn access）
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import Base, engine
from app.api import auth, workspace, workspace_settings, workspace_variables, datasource, studio, workflow, integration, datamap, quality, operation, probe
from app.api import scheduler as scheduler_api
from app.api import audit
from app.api import streaming
from app.api import admin_rbac, admin_integration
from app.api import data_service, data_service_open
from app.api import approval
from app.models import rbac_models  # noqa: F401  — 注册 RBAC 表
from app.models.workspace import PlatformIntegration, FlinkSessionProfile, WorkspacePlatformIntegration, PublishApproval, WorkspaceVariable  # noqa: F401
from app.models import data_service as data_service_models  # noqa: F401

Base.metadata.create_all(bind=engine)

_log = logging.getLogger(__name__)


def _apply_reset_admin_password_if_configured(db) -> None:
    """与正在启动的后端共用同一 Session / 元数据库连接（`DATABASE_URL` 或 `INFRA_GIDO_DB_*` 解析结果），避免脚本与 uvicorn 连错库。"""
    raw = settings.RESET_ADMIN_PASSWORD
    if not raw or not str(raw).strip():
        return
    from app.models.workspace import User
    from app.core.security import get_password_hash

    plain = str(raw).strip()
    user = db.query(User).filter(User.username == "admin").first()
    if user:
        user.hashed_password = get_password_hash(plain)
        user.is_active = True
    else:
        db.add(
            User(
                username="admin",
                email="admin@gido.com",
                full_name="管理员",
                hashed_password=get_password_hash(plain),
                is_admin=True,
                is_active=True,
            )
        )
    db.commit()
    from app.services.workspace_default import ensure_default_workspace_membership

    admin_u = db.query(User).filter(User.username == "admin").first()
    if admin_u:
        ensure_default_workspace_membership(db, admin_u)
    _log.warning(
        "已根据 RESET_ADMIN_PASSWORD 写入管理员账号，请立即登录验证并从 .env 删除该变量（勿提交到仓库）。"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services import scheduler, executor
    from app.services.rbac_seed import (
        migrate_schema,
        migrate_platform_integration,
        migrate_platform_integration_flink,
        migrate_default_workspace_to_infras,
        migrate_workspace_owner_members,
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
        migrate_dw_workspace_variables,
        migrate_dw_users_avatar,
        run_rbac_bootstrap,
    )
    from app.core.database import SessionLocal
    migrate_schema(engine)
    migrate_dw_users_avatar(engine)
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
    migrate_dw_workspace_variables(engine)
    db = SessionLocal()
    try:
        run_rbac_bootstrap(db)
        _apply_reset_admin_password_if_configured(db)
        from app.services.ds_runtime import refresh_ds_client
        from app.services.flink_runtime import refresh_flink_client

        refresh_ds_client(db)
        refresh_flink_client(db)
    finally:
        db.close()
    # 自动生成内部 token（供 DS worker 回调使用）
    _ensure_internal_token()
    scheduler.start()
    from app.services.integration_cdc import start_cdc_manager

    start_cdc_manager()
    db2 = SessionLocal()
    try:
        from app.services.ds_runtime import get_dolphin_runtime
        ds_on = get_dolphin_runtime(db2).enabled
    finally:
        db2.close()
    if not ds_on:
        executor.start()
    yield
    if not ds_on:
        executor.stop()
    from app.services.integration_cdc import stop_cdc_manager

    stop_cdc_manager()
    try:
        from app.services.flink_operator_ui_tunnel import release_all_ui_tunnels

        release_all_ui_tunnels()
    except Exception:
        pass
    scheduler.stop()


def _ensure_internal_token():
    """admin 账号的长期 token，写入 .env 中供 DS worker 回调使用"""
    from app.core.config import settings
    from app.core.security import create_access_token
    if not settings.INTERNAL_TOKEN:
        token = create_access_token({"sub": "admin"}, expires_days=3650)
        settings.INTERNAL_TOKEN = token
        # 写入 .env
        import os
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        env_path = os.path.abspath(env_path)
        lines = []
        if os.path.exists(env_path):
            with open(env_path) as f:
                lines = f.readlines()
        lines = [l for l in lines if not l.startswith('INTERNAL_TOKEN=')]
        lines.append(f'INTERNAL_TOKEN={token}\n')
        with open(env_path, 'w') as f:
            f.writelines(lines)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="玑渡 GIDO — 开源大数据开发、调度与数据服务套件（璇玑指引 · 数据有渡）",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(workspace.router, prefix="/api")
app.include_router(workspace_settings.router, prefix="/api")
app.include_router(workspace_variables.router, prefix="/api")
app.include_router(datasource.router, prefix="/api")
app.include_router(studio.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(integration.router, prefix="/api")
app.include_router(datamap.router, prefix="/api")
app.include_router(quality.router, prefix="/api")
app.include_router(operation.router, prefix="/api")
app.include_router(probe.router, prefix="/api")
app.include_router(scheduler_api.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(streaming.router, prefix="/api")
app.include_router(admin_rbac.router, prefix="/api")
app.include_router(admin_integration.router, prefix="/api")
app.include_router(data_service.router, prefix="/api")
app.include_router(data_service_open.open_router, prefix="/api")
app.include_router(approval.router, prefix="/api")


@app.get("/")
def root():
    return {"message": f"欢迎使用 {settings.APP_NAME}", "version": settings.APP_VERSION, "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "healthy"}

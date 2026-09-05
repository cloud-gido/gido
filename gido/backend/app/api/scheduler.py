# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.access import require_platform_manager
from app.models.workspace import User
from app.core.config import settings
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client

router = APIRouter(prefix="/scheduler", tags=["调度器"])


@router.get("/cron/preview")
def preview_cron(
    cron: str,
    count: int = 5,
    current_user: User = Depends(get_current_user),
):
    """
    预览 Cron 最近若干次执行时间（时区 Asia/Shanghai，与 DolphinScheduler 默认一致）。
    入参为 GIDO 使用的 5 段 Linux cron；响应同时给出发布到 DS 时的 Quartz 表达式。
    """
    from app.services.cron_utils import preview_next_runs

    try:
        linux, quartz, times = preview_next_runs(cron, count=count)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "cron": linux,
        "quartz_cron": quartz,
        "timezone": "Asia/Shanghai",
        "next_times": times,
        "count": len(times),
    }


@router.post("/reload")
def reload_scheduler(_: None = Depends(require_platform_manager)):
    """重新加载本地 APScheduler 调度任务（DS 未启用时使用）。仅平台管理员。"""
    from app.services import scheduler as svc_scheduler
    svc_scheduler.reload_schedules()
    return {"message": "调度器已重载"}


@router.get("/jobs")
def list_jobs(_: None = Depends(require_platform_manager)):
    """查看本地调度任务列表。仅平台管理员。"""
    from app.services.scheduler import scheduler as apscheduler
    jobs = [
        {"id": job.id, "name": job.name,
         "next_run": str(job.next_run_time) if job.next_run_time else None,
         "trigger": str(job.trigger)}
        for job in apscheduler.get_jobs()
    ]
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/ds/status")
def ds_status(db: Session = Depends(get_db), _: None = Depends(require_platform_manager)):
    """检查生产调度引擎连通性与同步健康度。仅平台管理员。"""
    cfg = get_dolphin_runtime(db)
    from app.models.workspace import WorkflowInstance

    last_sync_time = db.query(func.max(WorkflowInstance.last_synced_at)).scalar()
    recent_sync_error = (
        db.query(WorkflowInstance.scheduler_error)
        .filter(WorkflowInstance.scheduler_error.isnot(None))
        .order_by(func.coalesce(WorkflowInstance.last_synced_at, WorkflowInstance.created_at).desc(), WorkflowInstance.id.desc())
        .first()
    )
    if not cfg.enabled:
        return {
            "enabled": False,
            "connected": False,
            "auth_status": "unconfigured",
            "project_status": "unconfigured",
            "polling_status": "disabled",
            "webhook_configured": bool(settings.INTERNAL_TOKEN),
            "last_sync_time": last_sync_time,
            "last_sync_result": "生产调度引擎未启用",
        }
    from app.services.dolphin import ds_client
    try:
        refresh_ds_client(db)
        project_code = ds_client.get_or_create_project()
        return {
            "enabled": True,
            "connected": True,
            "status": "connected",
            "auth_status": "ok",
            "project_status": "exists",
            "polling_status": "last_run_failed" if recent_sync_error else "ok",
            "webhook_configured": bool(settings.INTERNAL_TOKEN),
            "last_sync_time": last_sync_time,
            "last_sync_result": recent_sync_error[0] if recent_sync_error else "ok",
            "project_code": project_code,
            "scheduler_url": cfg.url,
            "project_name": cfg.project_name,
        }
    except Exception as e:
        msg = str(e)
        lowered = msg.lower()
        auth_status = "failed" if "401" in lowered or "unauthorized" in lowered or "token" in lowered else "unknown"
        return {
            "enabled": True,
            "connected": False,
            "status": "error",
            "auth_status": auth_status,
            "project_status": "api_error",
            "polling_status": "last_run_failed",
            "webhook_configured": bool(settings.INTERNAL_TOKEN),
            "last_sync_time": last_sync_time,
            "last_sync_result": msg,
            "message": msg,
        }


@router.post("/ds/sync-instances")
def sync_ds_instances(_: None = Depends(require_platform_manager)):
    """
    主动同步 DS 流程实例：
    1) 按已发布工作流（dag 内 ds_process_code）从 Dolphin 拉最近流程实例入库/更新（含定时调度、未经过 GIDO /run 的运行）；
    2) 拉任务实例填充运维节点明细；
    3) 对库内最近含 ds: 的实例调详情 API 补 commandType / 终态。
    建议由定时任务分钟级调用；运维页「同步 Dolphin 触发类型」亦调用本接口。
    仅平台管理员。
    """
    from app.core.database import SessionLocal
    from app.services.dolphin import ds_client
    from app.services.dolphin_instance_sync import patch_instances_from_ds_detail, sync_from_dolphin_definitions

    db = SessionLocal()
    try:
        if not get_dolphin_runtime(db).enabled:
            return {"message": "DS 未启用", "synced": 0, "command_types_filled": 0}
        refresh_ds_client(db)
        ing = sync_from_dolphin_definitions(db, ds_client)
        checked, synced, cmd_detail = patch_instances_from_ds_detail(db, ds_client, limit=100)
        return {
            "message": "同步完成",
            "definitions_scanned": ing["definitions_scanned"],
            "ingested": ing["ingested"],
            "updated_from_ds": ing["updated_from_ds"],
            "node_rows_touched": ing["node_rows_touched"],
            "synced": synced,
            "checked": checked,
            "command_types_filled": ing["command_types_filled"] + cmd_detail,
        }
    finally:
        db.close()


@router.post("/ds/webhook")
def ds_webhook(payload: dict, current_user: User = Depends(get_current_user)):
    """
    接收 DolphinScheduler Alert Webhook 回调，自动更新实例状态
    DS 告警配置: POST http://gido-backend:8001/api/scheduler/ds/webhook
    payload 示例: {"processInstanceId": 123, "state": "SUCCESS"}
    """
    from app.core.database import SessionLocal
    from app.models.workspace import WorkflowInstance
    from app.services.dolphin import map_dolphin_process_instance_state
    from datetime import datetime

    ds_instance_id = payload.get("processInstanceId")
    ds_state = payload.get("state", "")
    if not ds_instance_id:
        return {"message": "ignored"}

    dw_status = map_dolphin_process_instance_state(ds_state)
    if dw_status == "running":
        return {"message": "still running"}

    db = SessionLocal()
    try:
        inst = db.query(WorkflowInstance).filter(
            or_(
                WorkflowInstance.scheduler_instance_id == str(ds_instance_id),
                WorkflowInstance.trigger_type.like(f"%ds:{ds_instance_id}%"),
            )
        ).first()
        if inst:
            inst.scheduler_engine = "dolphin"
            inst.scheduler_instance_id = str(ds_instance_id)
            inst.status = dw_status
            inst.finished_at = datetime.utcnow()
            db.commit()
            return {"message": "updated", "instance_id": inst.id, "status": dw_status}
        return {"message": "instance not found"}
    finally:
        db.close()


@router.post("/callback/dolphin")
def dolphin_scheduler_callback(payload: dict, x_internal_token: str = Header(default="")):
    """
    调度引擎回调：Dolphin Alert/Webhook 可在实例结束时调用本接口。
    推荐地址：POST /api/scheduler/callback/dolphin，Header: X-Internal-Token。
    """
    if settings.INTERNAL_TOKEN and x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="invalid internal token")

    from app.core.database import SessionLocal
    from app.models.workspace import Workflow, WorkflowInstance
    from app.services.dolphin import map_dolphin_process_instance_state
    from app.services.dolphin_instance_sync import refresh_ds_workflow_instance_from_dolphin
    from datetime import datetime

    raw_id = (
        payload.get("scheduler_instance_id")
        or payload.get("processInstanceId")
        or payload.get("process_instance_id")
        or payload.get("instanceId")
        or payload.get("id")
    )
    if raw_id is None or str(raw_id).strip() == "":
        return {"message": "ignored", "reason": "missing scheduler_instance_id"}
    scheduler_instance_id = str(raw_id).strip()
    raw_state = payload.get("state") or payload.get("status") or payload.get("executionStatus") or ""
    dw_status = map_dolphin_process_instance_state(raw_state)
    project_id = (
        payload.get("scheduler_project_id")
        or payload.get("projectCode")
        or payload.get("project_code")
        or payload.get("projectId")
    )
    definition_id = (
        payload.get("scheduler_definition_id")
        or payload.get("processDefinitionCode")
        or payload.get("process_definition_code")
        or payload.get("workflowDefinitionCode")
    )

    db = SessionLocal()
    try:
        q = db.query(WorkflowInstance).filter(
            or_(
                WorkflowInstance.scheduler_instance_id == scheduler_instance_id,
                WorkflowInstance.trigger_type.like(f"%ds:{scheduler_instance_id}%"),
            )
        )
        if project_id is not None and str(project_id).strip():
            q = q.filter(
                or_(
                    WorkflowInstance.scheduler_project_id == str(project_id).strip(),
                    WorkflowInstance.scheduler_project_id.is_(None),
                )
            )
        if definition_id is not None and str(definition_id).strip():
            q = q.filter(
                or_(
                    WorkflowInstance.scheduler_definition_id == str(definition_id).strip(),
                    WorkflowInstance.scheduler_definition_id.is_(None),
                )
            )
        inst = q.order_by(WorkflowInstance.id.desc()).first()
        if not inst:
            return {"message": "instance not found", "scheduler_instance_id": scheduler_instance_id}
        inst.scheduler_engine = "dolphin"
        if project_id is not None and str(project_id).strip():
            inst.scheduler_project_id = str(project_id).strip()
        if definition_id is not None and str(definition_id).strip():
            inst.scheduler_definition_id = str(definition_id).strip()
        inst.scheduler_instance_id = scheduler_instance_id
        inst.scheduler_run_key = (
            f"dolphin:{inst.scheduler_project_id or ''}:{inst.scheduler_definition_id or ''}:{scheduler_instance_id}"
        )[:128]
        inst.scheduler_state_raw = str(raw_state)[:128] if raw_state is not None else None
        inst.scheduler_error = None
        inst.last_synced_at = datetime.utcnow()
        old_status = inst.status
        inst.status = dw_status
        if dw_status != "running":
            inst.finished_at = datetime.utcnow()
        elif dw_status == "running":
            inst.finished_at = None
        if dw_status == "failed" and old_status != "failed":
            try:
                from app.services.alert_center import open_instance_alert

                open_instance_alert(db, workflow_instance=inst, message=f"实例 #{inst.id} 执行失败")
            except Exception:
                pass
        db.commit()

        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        if wf:
            try:
                refresh_ds_client(db, wf.workspace_id)
                refresh_ds_workflow_instance_from_dolphin(db, wf.workspace_id, inst.id)
            except Exception:
                db.rollback()
        return {"message": "updated", "instance_id": inst.id, "status": dw_status}
    finally:
        db.close()

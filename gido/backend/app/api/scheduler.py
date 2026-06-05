# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client

router = APIRouter(prefix="/scheduler", tags=["调度器"])


@router.post("/reload")
def reload_scheduler(current_user: User = Depends(get_current_user)):
    """重新加载本地 APScheduler 调度任务（DS 未启用时使用）"""
    from app.services import scheduler as svc_scheduler
    svc_scheduler.reload_schedules()
    return {"message": "调度器已重载"}


@router.get("/jobs")
def list_jobs(current_user: User = Depends(get_current_user)):
    """查看本地调度任务列表"""
    from app.services.scheduler import scheduler as apscheduler
    jobs = [
        {"id": job.id, "name": job.name,
         "next_run": str(job.next_run_time) if job.next_run_time else None,
         "trigger": str(job.trigger)}
        for job in apscheduler.get_jobs()
    ]
    return {"jobs": jobs, "count": len(jobs)}


@router.get("/ds/status")
def ds_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """检查 DolphinScheduler 连通性"""
    cfg = get_dolphin_runtime(db)
    if not cfg.enabled:
        return {"enabled": False, "message": "DolphinScheduler 未启用（系统管理或 DS_ENABLED）"}
    from app.services.dolphin import ds_client
    try:
        refresh_ds_client(db)
        project_code = ds_client.get_or_create_project()
        return {
            "enabled": True,
            "status": "connected",
            "project_code": project_code,
            "ds_url": cfg.url,
            "project_name": cfg.project_name,
        }
    except Exception as e:
        return {"enabled": True, "status": "error", "message": str(e)}


@router.post("/ds/sync-instances")
def sync_ds_instances(current_user: User = Depends(get_current_user)):
    """
    主动同步 DS 流程实例：
    1) 按已发布工作流（dag 内 ds_process_code）从 Dolphin 拉最近流程实例入库/更新（含定时调度、未经过 GIDO /run 的运行）；
    2) 拉任务实例填充运维节点明细；
    3) 对库内最近含 ds: 的实例调详情 API 补 commandType / 终态。
    建议由定时任务分钟级调用；运维页「同步 Dolphin 触发类型」亦调用本接口。
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
            WorkflowInstance.trigger_type.like(f"%ds:{ds_instance_id}%")
        ).first()
        if inst:
            inst.status = dw_status
            inst.finished_at = datetime.utcnow()
            db.commit()
            return {"message": "updated", "instance_id": inst.id, "status": dw_status}
        return {"message": "instance not found"}
    finally:
        db.close()

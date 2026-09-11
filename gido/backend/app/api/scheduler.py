# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.access import is_platform_admin, require_platform_manager
from app.core import perm_codes as PC
from app.models.workspace import User
from app.core.config import settings
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
from app.services.instance_override import is_status_pinned
from app.services.rbac import assert_workspace_data_capability

router = APIRouter(prefix="/scheduler", tags=["调度器"])
logger = logging.getLogger(__name__)


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
def sync_ds_instances(
    workspace_id: Optional[int] = Query(None, description="不传则同步全部已发布工作流（仅平台管理员）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    立即采集一轮运行数据（后台采集任务之外的手动兜底）。带 workspace_id 时空间开发者即可采集本空间。
    """
    from app.core.database import SessionLocal
    from app.services.run_collector import collect_runs, collector_health

    if workspace_id is not None:
        assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    elif not is_platform_admin(current_user):
        raise HTTPException(status_code=403, detail="采集全部工作空间需要平台管理员，或传入 workspace_id")

    own = SessionLocal()
    try:
        out = collect_runs(own, workspace_id=workspace_id)
        if not out.get("collected"):
            # 连点几次「立即采集」不该把并发采集打出去：后面几次会拿不到锁而跳过，
            # 这里要说清是「已在采集」而不是「调度没开」，否则用户会以为配置坏了
            skipped = out.get("reason") == "already_running"
            return {
                "message": "已有一轮采集正在进行，本次跳过" if skipped else "生产调度未启用",
                "collected": False,
                "skipped": skipped,
                "synced": 0,
                "command_types_filled": 0,
                "collector": collector_health(own),
            }
        return {
            "message": "采集完成",
            "collected": True,
            "definitions_scanned": out["definitions_scanned"],
            "ingested": out["ingested"],
            "updated_from_ds": out["updated_from_ds"],
            "node_rows_touched": out["node_rows_touched"],
            "skipped_unbound": out.get("skipped_unbound", 0),
            "synced": out["finalized_from_detail"],
            "checked": out["detail_checked"],
            "command_types_filled": out["command_types_filled"],
            "collector": collector_health(own),
        }
    finally:
        own.close()


@router.post("/ds/webhook")
def ds_webhook(
    payload: dict,
    x_internal_token: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """
    Dolphin HTTP 回调入口（与 /callback/dolphin 相同，不要求登录态）。
    Header: X-Internal-Token 或 Authorization: Bearer <INTERNAL_TOKEN>。
    """
    token = (x_internal_token or "").strip()
    if not token and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    return dolphin_scheduler_callback(payload, x_internal_token=token)


@router.post("/callback/dolphin")
def dolphin_scheduler_callback(payload: dict, x_internal_token: str = Header(default="")):
    """
    调度引擎回调：Dolphin Alert/Webhook 可在实例结束时调用本接口。
    推荐地址：POST /api/scheduler/callback/dolphin，Header: X-Internal-Token。
    """
    # 未配置 INTERNAL_TOKEN 时必须拒绝：否则任何能连到后端的人都能伪造实例状态与告警
    if not settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=503, detail="internal token not configured")
    if x_internal_token != settings.INTERNAL_TOKEN:
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
            from app.services.dolphin_instance_sync import ingest_ds_instance_from_callback

            inst = ingest_ds_instance_from_callback(
                db,
                scheduler_instance_id=scheduler_instance_id,
                dw_status=dw_status,
                raw_state=raw_state,
                project_id=project_id,
                definition_id=definition_id,
            )
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
        # 人工置过状态的实例只更新引擎快照，状态和告警都保持人工结论
        if not is_status_pinned(inst):
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
                    logger.warning("回调开启失败告警异常 instance_id=%s", inst.id, exc_info=True)
            elif dw_status == "success" and old_status == "failed":
                # 轮询采集会做恢复闭环，回调路径此前漏了，重跑成功后告警一直挂着
                try:
                    from app.services.alert_center import resolve_instance_alerts_on_recovery

                    resolve_instance_alerts_on_recovery(db, inst)
                except Exception:
                    logger.warning("回调关闭恢复告警异常 instance_id=%s", inst.id, exc_info=True)
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

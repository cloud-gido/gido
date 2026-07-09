# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import Optional
from datetime import datetime, timedelta
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import WorkflowInstance, NodeInstance, TaskNode, Workflow, User
from app.services.rbac import assert_workspace_data_capability, require_node_instance
from app.services.workflow_trigger_display import format_trigger_type_label, parse_dolphin_process_instance_id
from app.services.ds_runtime import get_dolphin_runtime
from app.services.dolphin_instance_sync import (
    refresh_ds_workflow_instance_from_dolphin,
    refresh_running_ds_instances_for_workspace,
)

router = APIRouter(prefix="/operation", tags=["运维中心"])
_log = logging.getLogger(__name__)

# 仅统计/展示「工作流提交」产生的实例：NodeInstance 必须挂 WorkflowInstance（排除数据开发里单节点试跑）


def _safe_refresh_ds_running(db: Session, workspace_id: int) -> None:
    """Dolphin 不可达时不阻断运维列表。"""
    try:
        refresh_running_ds_instances_for_workspace(db, workspace_id, limit=35)
    except Exception:
        _log.warning("refresh_running_ds_instances_for_workspace failed ws=%s", workspace_id, exc_info=True)


def _require_workflow_instance(
    db: Session,
    current_user: User,
    workspace_id: int,
    inst_id: int,
    capability: str,
) -> tuple[WorkflowInstance, Workflow]:
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", capability)
    inst = (
        db.query(WorkflowInstance)
        .join(Workflow)
        .filter(WorkflowInstance.id == inst_id, Workflow.workspace_id == workspace_id)
        .first()
    )
    if not inst:
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return inst, wf


@router.get("/overview")
def get_overview(
    workspace_id: int,
    include_manual_development_runs: bool = Query(
        False,
        description="已废弃：实例中心仅展示生产工作流实例；开发/探查请到运行历史",
        deprecated=True,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    工作流级运行概况（`WorkflowInstance` × 当前工作区），不含数据开发单节点试跑。
    - 今日实例：`created_at` 为当日 0 点（UTC）起新建的工作流实例条数
    - 运行中 / 成功 / 失败：按工作流实例的 `status` 计数
    - 成功率：成功 / (成功 + 失败)，无失败且无成功时为 N/A
    """
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    _safe_refresh_ds_running(db, workspace_id)
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    q = db.query(WorkflowInstance).join(Workflow).filter(Workflow.workspace_id == workspace_id)
    total = q.count()
    today = q.filter(WorkflowInstance.created_at >= today_start).count()
    running = q.filter(WorkflowInstance.status == "running").count()
    failed = q.filter(WorkflowInstance.status == "failed").count()
    success = q.filter(WorkflowInstance.status == "success").count()

    # 近 7 日实例趋势（按 UTC 日期）
    trend_start = today_start - timedelta(days=6)
    trend_rows = (
        q.filter(WorkflowInstance.created_at >= trend_start)
        .with_entities(
            func.date(WorkflowInstance.created_at).label("d"),
            WorkflowInstance.status,
            func.count(WorkflowInstance.id),
        )
        .group_by(func.date(WorkflowInstance.created_at), WorkflowInstance.status)
        .all()
    )
    daily_map: dict = {}
    for d, st, cnt in trend_rows:
        key = str(d)
        if key not in daily_map:
            daily_map[key] = {"date": key, "total": 0, "success": 0, "failed": 0, "running": 0}
        daily_map[key]["total"] += cnt
        if st in daily_map[key]:
            daily_map[key][st] += cnt
    daily_trend = []
    for i in range(7):
        day = (trend_start + timedelta(days=i)).date()
        key = str(day)
        daily_trend.append(daily_map.get(key, {"date": key, "total": 0, "success": 0, "failed": 0, "running": 0}))

    status_distribution = [
        {"status": "success", "count": success},
        {"status": "failed", "count": failed},
        {"status": "running", "count": running},
        {"status": "pending", "count": q.filter(WorkflowInstance.status == "pending").count()},
        {"status": "killed", "count": q.filter(WorkflowInstance.status == "killed").count()},
    ]

    from app.services.publish_approval import pending_approval_count

    return {
        "total_instances": total,
        "today_instances": today,
        "running": running,
        "failed": failed,
        "success": success,
        "success_rate": f"{int(success / (success + failed) * 100)}%" if (success + failed) > 0 else "N/A",
        "daily_trend": daily_trend,
        "status_distribution": status_distribution,
        "pending_approvals": pending_approval_count(db, workspace_id),
    }


@router.get("/instances")
def list_all_instances(
    workspace_id: int,
    status: Optional[str] = None,
    business_date: Optional[str] = None,
    today_only: bool = Query(False, description="仅 created_at ≥ 当日 0 点(UTC) 的实例，与概览「今日实例」一致"),
    include_manual_development_runs: bool = Query(
        False,
        description="已废弃：实例中心仅展示生产工作流实例",
        deprecated=True,
    ),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    _safe_refresh_ds_running(db, workspace_id)
    q = db.query(WorkflowInstance).join(Workflow).filter(Workflow.workspace_id == workspace_id)
    if status:
        q = q.filter(WorkflowInstance.status == status)
    if business_date:
        q = q.filter(WorkflowInstance.business_date == business_date)
    if today_only:
        today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
        q = q.filter(WorkflowInstance.created_at >= today_start)
    total = q.count()
    # 排序用 coalesce，避免各方言对 NULLS FIRST/LAST 差异（MySQL 无 NULLS LAST）
    instances = (
        q.order_by(
            desc(func.coalesce(WorkflowInstance.started_at, WorkflowInstance.created_at)),
            desc(WorkflowInstance.id),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    result = []
    for inst in instances:
        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        tt = inst.trigger_type
        dct = getattr(inst, "dolphin_command_type", None)
        node_rows = db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all()
        node_total = len(node_rows)
        running_nodes = [
            ni for ni in node_rows
            if ni.status in ("running", "pending")
        ]
        failed_nodes = [ni for ni in node_rows if ni.status == "failed"]
        node_names = {
            node.id: node.name
            for node in db.query(TaskNode).filter(TaskNode.id.in_([ni.node_id for ni in node_rows if ni.node_id])).all()
        } if node_rows else {}
        duration_seconds = None
        if inst.started_at and inst.finished_at:
            duration_seconds = int((inst.finished_at - inst.started_at).total_seconds())
        result.append({
            "id": inst.id,
            "workflow_id": wf.id if wf else None,
            "workflow_name": wf.name if wf else "",
            "status": inst.status,
            "trigger_type": tt,
            "dolphin_command_type": dct,
            "scheduler_engine": getattr(inst, "scheduler_engine", None) or "dolphin",
            "scheduler_instance_id": getattr(inst, "scheduler_instance_id", None),
            "trigger_label": format_trigger_type_label(tt, dct, getattr(inst, "scheduler_instance_id", None)),
            "dolphin_process_instance_id": parse_dolphin_process_instance_id(tt),
            "business_date": inst.business_date,
            "started_at": inst.started_at,
            "finished_at": inst.finished_at,
            "last_synced_at": getattr(inst, "last_synced_at", None),
            "scheduler_state_raw": getattr(inst, "scheduler_state_raw", None),
            "scheduler_error": getattr(inst, "scheduler_error", None),
            "node_total": node_total,
            "running_node_count": len(running_nodes),
            "failed_node_count": len(failed_nodes),
            "current_nodes": [node_names.get(ni.node_id, f"节点#{ni.node_id}") for ni in running_nodes[:5]],
            "failed_nodes": [node_names.get(ni.node_id, f"节点#{ni.node_id}") for ni in failed_nodes[:5]],
            "duration_seconds": duration_seconds,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": result}


@router.get("/node-instances")
def list_node_instances(
    workspace_id: int,
    status: Optional[str] = None,
    workflow_instance_id: Optional[int] = Query(None, description="仅某工作流实例下的节点行（下钻）"),
    include_manual_development_runs: bool = Query(
        False,
        description="已废弃：开发试跑请到运行历史",
        deprecated=True,
    ),
    page: int = 1,
    page_size: int = 20,
    include_studio_runs: bool = Query(
        False,
        description="已废弃：实例中心仅展示生产工作流节点；开发试跑请到运行历史",
        deprecated=True,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """节点级运行明细：仅工作流实例下的生产节点运行。"""
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    _safe_refresh_ds_running(db, workspace_id)
    if workflow_instance_id is not None:
        refresh_ds_workflow_instance_from_dolphin(db, workspace_id, workflow_instance_id)
    q = (
        db.query(NodeInstance)
        .join(TaskNode, NodeInstance.node_id == TaskNode.id)
        .join(WorkflowInstance, NodeInstance.workflow_instance_id == WorkflowInstance.id)
        .filter(TaskNode.workspace_id == workspace_id, NodeInstance.workflow_instance_id.isnot(None))
    )
    if workflow_instance_id is not None:
        q = q.filter(NodeInstance.workflow_instance_id == workflow_instance_id)
    if status:
        q = q.filter(NodeInstance.status == status)
    total = q.count()
    instances = (
        q.order_by(
            desc(
                func.coalesce(
                    WorkflowInstance.started_at,
                    NodeInstance.started_at,
                    WorkflowInstance.created_at,
                )
            ),
            desc(NodeInstance.id),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    result = []
    context = None
    if workflow_instance_id is not None:
        wf_inst_ctx = db.query(WorkflowInstance).filter(WorkflowInstance.id == workflow_instance_id).first()
        wf_ctx = db.query(Workflow).filter(Workflow.id == wf_inst_ctx.workflow_id).first() if wf_inst_ctx else None
        if wf_inst_ctx and wf_ctx:
            node_status_rows = (
                db.query(NodeInstance.status, func.count(NodeInstance.id))
                .filter(NodeInstance.workflow_instance_id == workflow_instance_id)
                .group_by(NodeInstance.status)
                .all()
            )
            context = {
                "workflow_instance_id": wf_inst_ctx.id,
                "workflow_id": wf_ctx.id,
                "workflow_name": wf_ctx.name,
                "status": wf_inst_ctx.status,
                "trigger_type": wf_inst_ctx.trigger_type,
                "dolphin_command_type": getattr(wf_inst_ctx, "dolphin_command_type", None),
                "trigger_label": format_trigger_type_label(
                    wf_inst_ctx.trigger_type,
                    getattr(wf_inst_ctx, "dolphin_command_type", None),
                    getattr(wf_inst_ctx, "scheduler_instance_id", None),
                ),
                "business_date": wf_inst_ctx.business_date,
                "started_at": wf_inst_ctx.started_at,
                "finished_at": wf_inst_ctx.finished_at,
                "last_synced_at": getattr(wf_inst_ctx, "last_synced_at", None),
                "scheduler_instance_id": getattr(wf_inst_ctx, "scheduler_instance_id", None),
                "scheduler_error": getattr(wf_inst_ctx, "scheduler_error", None),
                "node_status_distribution": [
                    {"status": st or "unknown", "count": cnt}
                    for st, cnt in node_status_rows
                ],
            }
    for ni in instances:
        node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first()
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first() if wf_inst else None
        tt = wf_inst.trigger_type if wf_inst else None
        dct = getattr(wf_inst, "dolphin_command_type", None) if wf_inst else None
        log_summary = ""
        if ni.status == "failed" and ni.log_content:
            log_summary = str(ni.log_content).strip().splitlines()[0][:300]
        result.append({
            "id": ni.id,
            "workflow_instance_id": ni.workflow_instance_id,
            "workflow_name": wf.name if wf else "",
            "trigger_type": tt or "",
            "dolphin_command_type": dct,
            "scheduler_engine": getattr(wf_inst, "scheduler_engine", None) if wf_inst else None,
            "scheduler_instance_id": getattr(wf_inst, "scheduler_instance_id", None) if wf_inst else None,
            "scheduler_task_instance_id": getattr(ni, "scheduler_task_instance_id", None),
            "scheduler_task_code": getattr(ni, "scheduler_task_code", None),
            "trigger_label": format_trigger_type_label(tt, dct, getattr(wf_inst, "scheduler_instance_id", None)) if wf_inst else "数据开发试跑",
            "dolphin_process_instance_id": parse_dolphin_process_instance_id(tt) if wf_inst else None,
            "node_name": node.name if node else "",
            "node_type": node.node_type if node else "",
            "status": ni.status,
            "started_at": ni.started_at,
            "finished_at": ni.finished_at,
            "retry_count": ni.retry_count,
            "log_summary": log_summary,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": result, "context": context}


@router.get("/node-instances/{ni_id}/log")
def get_node_log(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id)
    scheduler_instance_id = None
    from app.services.scheduler_ops import fetch_node_log_payload

    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            scheduler_id = getattr(wf_inst, "scheduler_instance_id", None)
            if scheduler_id and str(scheduler_id).strip().lstrip("-").isdigit():
                scheduler_instance_id = int(str(scheduler_id).strip())
            else:
                scheduler_instance_id = parse_dolphin_process_instance_id(wf_inst.trigger_type)
    try:
        payload = fetch_node_log_payload(db, ni)
    except Exception as e:
        _log.warning("fetch_node_log_payload failed ni=%s: %s", ni_id, e, exc_info=True)
        payload = {
            "log": ni.log_content or "",
            "message": "拉取调度日志异常，展示 GIDO 本地记录。",
            "source": "gido",
            "status": "error",
        }
    return {
        "log": payload.get("log") or "",
        "log_source_hint": payload.get("message") or "",
        "log_source": payload.get("source"),
        "log_status": payload.get("status"),
        "scheduler_instance_id": scheduler_instance_id,
        "dolphin_process_instance_id": scheduler_instance_id,
        "dolphin_process_instance_url": None,
        "scheduler_task_instance_id": getattr(ni, "scheduler_task_instance_id", None),
        "scheduler_task_code": getattr(ni, "scheduler_task_code", None),
    }


@router.post("/workflows/{workflow_id}/instances/{inst_id}/stop")
def stop_workflow_instance(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_WRITE)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            from app.services.scheduler_ops import stop_workflow_instance_via_scheduler

            stop_workflow_instance_via_scheduler(db, inst)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎停止失败: {e}")
    inst.status = "killed"
    inst.finished_at = datetime.utcnow()
    for node_inst in db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all():
        if node_inst.status in ("pending", "running"):
            node_inst.status = "killed"
            node_inst.finished_at = datetime.utcnow()
    db.commit()
    return {"message": "已停止工作流实例", "instance_id": inst.id, "status": inst.status}


@router.post("/workflows/{workflow_id}/instances/{inst_id}/refresh")
def refresh_workflow_instance(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_READ)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    refresh_ds_workflow_instance_from_dolphin(db, wf.workspace_id, inst.id)
    db.refresh(inst)
    return {
        "message": "已刷新工作流实例",
        "instance_id": inst.id,
        "status": inst.status,
        "last_synced_at": getattr(inst, "last_synced_at", None),
        "scheduler_error": getattr(inst, "scheduler_error", None),
    }


@router.post("/workflows/{workflow_id}/instances/{inst_id}/rerun")
def rerun_workflow_instance(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_WRITE)
    from app.api.workflow import rerun_instance

    return rerun_instance(workflow_id, inst_id, db, current_user)


@router.post("/workflows/{workflow_id}/instances/{inst_id}/retry-failed-nodes")
def retry_failed_nodes(
    workflow_id: int,
    inst_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    inst, wf = _require_workflow_instance(db, current_user, workspace_id, inst_id, PC.GIDO_BATCH_OPERATION_WRITE)
    if int(inst.workflow_id) != int(workflow_id):
        raise HTTPException(status_code=404, detail="工作流实例不存在")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            from app.services.scheduler_ops import retry_failed_nodes_via_scheduler

            retry_failed_nodes_via_scheduler(db, inst)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎重试失败: {e}")
    inst.status = "running"
    inst.finished_at = None
    for node_inst in db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id, NodeInstance.status == "failed").all():
        node_inst.status = "running"
        node_inst.finished_at = None
        node_inst.retry_count = (node_inst.retry_count or 0) + 1
    db.commit()
    return {"message": "已提交失败节点重试", "instance_id": inst.id}


@router.post("/node-instances/{ni_id}/kill")
def kill_node_instance(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id, "developer", PC.GIDO_BATCH_OPERATION_WRITE)
    from app.services.scheduler_ops import kill_node_via_scheduler

    wf = None
    wf_inst = None
    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if wf and get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            kill_node_via_scheduler(db, ni, workspace_id=wf.workspace_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎终止失败: {e}")
    ni.status = "killed"
    ni.finished_at = datetime.utcnow()
    if wf_inst:
        wf_inst.status = "killed"
        wf_inst.finished_at = datetime.utcnow()
    db.commit()
    return {"message": "已终止"}


@router.post("/node-instances/{ni_id}/retry")
def retry_node_instance(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id, "developer", PC.GIDO_BATCH_OPERATION_WRITE)
    from app.services.scheduler_ops import retry_node_via_scheduler

    wf = None
    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if wf and get_dolphin_runtime(db, wf.workspace_id).enabled:
        try:
            retry_node_via_scheduler(db, ni, workspace_id=wf.workspace_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"调度引擎重试失败: {e}")
    ni.status = "running" if wf and get_dolphin_runtime(db, wf.workspace_id).enabled else "pending"
    ni.finished_at = None
    ni.retry_count += 1
    if wf_inst := db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first():
        if wf_inst.status in ("failed", "success", "killed"):
            wf_inst.status = "running"
            wf_inst.finished_at = None
    db.commit()
    return {"message": "已提交重试", "retry_count": ni.retry_count}


@router.get("/alerts")
def get_alerts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    failed = db.query(WorkflowInstance).join(Workflow).filter(
        Workflow.workspace_id == workspace_id,
        WorkflowInstance.status == "failed",
        WorkflowInstance.created_at >= datetime.utcnow() - timedelta(hours=24),
    ).all()
    alerts = []
    for inst in failed:
        wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
        alerts.append({
            "type": "workflow_failed",
            "workflow_name": wf.name if wf else "",
            "instance_id": inst.id,
            "business_date": inst.business_date,
            "time": inst.finished_at,
        })
    return {"alerts": alerts, "count": len(alerts)}

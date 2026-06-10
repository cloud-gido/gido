# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, desc, func
from typing import Optional
from datetime import datetime, timedelta
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import WorkflowInstance, NodeInstance, TaskNode, Workflow, User
from app.services.rbac import assert_workspace_data_capability, require_node_instance
from app.services.workflow_trigger_display import format_trigger_type_label, parse_dolphin_process_instance_id
from app.services.dolphin import dolphin_process_instance_console_url
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


def _exclude_manual_dev_filter():
    """数据开发「立即运行」：manual / manual|ds:… — 默认不进入运维统计与列表。"""
    return or_(WorkflowInstance.trigger_type == "manual", WorkflowInstance.trigger_type.like("manual|%"))


@router.get("/overview")
def get_overview(
    workspace_id: int,
    include_manual_development_runs: bool = Query(
        False,
        description="为 true 时统计中含数据开发「立即运行」（manual / manual|ds:），默认 false 与 Dolphin 调度运维分离",
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
    if not include_manual_development_runs:
        q = q.filter(~_exclude_manual_dev_filter())
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
    include_manual_development_runs: bool = Query(False, description="是否包含数据开发立即运行（manual / manual|ds:）"),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    _safe_refresh_ds_running(db, workspace_id)
    q = db.query(WorkflowInstance).join(Workflow).filter(Workflow.workspace_id == workspace_id)
    if not include_manual_development_runs:
        q = q.filter(~_exclude_manual_dev_filter())
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
        result.append({
            "id": inst.id,
            "workflow_name": wf.name if wf else "",
            "status": inst.status,
            "trigger_type": tt,
            "dolphin_command_type": dct,
            "trigger_label": format_trigger_type_label(tt, dct),
            "dolphin_process_instance_id": parse_dolphin_process_instance_id(tt),
            "business_date": inst.business_date,
            "started_at": inst.started_at,
            "finished_at": inst.finished_at,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": result}


@router.get("/node-instances")
def list_node_instances(
    workspace_id: int,
    status: Optional[str] = None,
    workflow_instance_id: Optional[int] = Query(None, description="仅某工作流实例下的节点行（下钻）"),
    include_manual_development_runs: bool = Query(False, description="是否包含立即运行对应节点行（默认 false）"),
    page: int = 1,
    page_size: int = 20,
    include_studio_runs: bool = Query(False, description="为 true 时包含数据开发单节点试跑（兼容旧运维视图，默认 false）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """节点级运行明细。默认仅工作流实例下的节点运行；可选包含 Studio 试跑以做对照/排障。"""
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    _safe_refresh_ds_running(db, workspace_id)
    if workflow_instance_id is not None:
        refresh_ds_workflow_instance_from_dolphin(db, workspace_id, workflow_instance_id)
    if include_studio_runs:
        q = db.query(NodeInstance).join(TaskNode).join(Workflow, TaskNode.workspace_id == Workflow.workspace_id).filter(
            TaskNode.workspace_id == workspace_id
        )
        if not include_manual_development_runs:
            allowed_wf = (
                db.query(WorkflowInstance.id)
                .join(Workflow)
                .filter(Workflow.workspace_id == workspace_id, ~_exclude_manual_dev_filter())
            )
            q = q.filter(
                or_(
                    NodeInstance.workflow_instance_id.is_(None),
                    NodeInstance.workflow_instance_id.in_(allowed_wf),
                )
            )
    else:
        q = (
            db.query(NodeInstance)
            .join(TaskNode, NodeInstance.node_id == TaskNode.id)
            .join(WorkflowInstance, NodeInstance.workflow_instance_id == WorkflowInstance.id)
            .filter(TaskNode.workspace_id == workspace_id, NodeInstance.workflow_instance_id.isnot(None))
        )
        if not include_manual_development_runs:
            q = q.filter(~_exclude_manual_dev_filter())
    if workflow_instance_id is not None:
        q = q.filter(NodeInstance.workflow_instance_id == workflow_instance_id)
    if status:
        q = q.filter(NodeInstance.status == status)
    total = q.count()
    if include_studio_runs:
        instances = (
            q.order_by(
                desc(func.coalesce(NodeInstance.started_at, NodeInstance.finished_at)),
                desc(NodeInstance.id),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
    else:
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
    for ni in instances:
        node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first()
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first() if wf_inst else None
        tt = wf_inst.trigger_type if wf_inst else None
        dct = getattr(wf_inst, "dolphin_command_type", None) if wf_inst else None
        result.append({
            "id": ni.id,
            "workflow_instance_id": ni.workflow_instance_id,
            "workflow_name": wf.name if wf else "",
            "trigger_type": tt or "",
            "dolphin_command_type": dct,
            "trigger_label": format_trigger_type_label(tt, dct) if wf_inst else "数据开发试跑",
            "dolphin_process_instance_id": parse_dolphin_process_instance_id(tt) if wf_inst else None,
            "node_name": node.name if node else "",
            "node_type": node.node_type if node else "",
            "status": ni.status,
            "started_at": ni.started_at,
            "finished_at": ni.finished_at,
            "retry_count": ni.retry_count,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": result}


@router.get("/node-instances/{ni_id}/log")
def get_node_log(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id)
    dolphin_process_instance_id = None
    dolphin_process_instance_url = None
    log_source_hint = "此处为节点在 GIDO Batch 侧记录的运行输出（例如 Studio 执行结果）。"
    if ni.workflow_instance_id:
        wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
        if wf_inst:
            dolphin_process_instance_id = parse_dolphin_process_instance_id(wf_inst.trigger_type)
            if dolphin_process_instance_id is not None:
                wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
                if wf:
                    dag = wf.dag_config or {}
                    pc = dag.get("ds_project_code")
                    if pc is not None:
                        try:
                            dolphin_process_instance_url = dolphin_process_instance_console_url(
                                int(pc), dolphin_process_instance_id, db, workspace_id=wf.workspace_id
                            )
                        except (TypeError, ValueError):
                            dolphin_process_instance_url = None
                log_source_hint = (
                    "此处日志来自 GIDO 节点执行（例如 Dolphin 中 SHELL 任务通过 curl 调 Studio 后写回）。"
                    "Dolphin Worker 上的脚本输出与其它任务类型的标准日志，请在 Dolphin「工作流实例 → 任务实例」中对照同一流程实例查看。"
                )
    return {
        "log": ni.log_content or "",
        "log_source_hint": log_source_hint,
        "dolphin_process_instance_id": dolphin_process_instance_id,
        "dolphin_process_instance_url": dolphin_process_instance_url,
    }


@router.post("/node-instances/{ni_id}/kill")
def kill_node_instance(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id, "developer", PC.GIDO_BATCH_OPERATION_WRITE)
    ni.status = "killed"
    ni.finished_at = datetime.utcnow()
    db.commit()
    return {"message": "已终止"}


@router.post("/node-instances/{ni_id}/retry")
def retry_node_instance(ni_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ni = require_node_instance(db, current_user, ni_id, "developer", PC.GIDO_BATCH_OPERATION_WRITE)
    ni.status = "pending"
    ni.retry_count += 1
    db.commit()
    return {"message": "已提交重试", "retry_count": ni.retry_count}


@router.get("/alerts")
def get_alerts(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_OPERATION_READ)
    failed = db.query(WorkflowInstance).join(Workflow).filter(
        Workflow.workspace_id == workspace_id,
        WorkflowInstance.status == "failed",
        WorkflowInstance.created_at >= datetime.utcnow() - timedelta(hours=24),
        ~_exclude_manual_dev_filter(),
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

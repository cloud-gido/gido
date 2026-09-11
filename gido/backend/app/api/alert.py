# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.access import is_platform_admin
from app.models.workspace import (
    AlertEvent,
    AlertNotificationConfig,
    AlertOnCallShift,
    NodeInstance,
    TaskNode,
    User,
    Workflow,
    WorkflowInstance,
    WorkflowSlaRule,
    Workspace,
    WorkspaceMember,
)
from app.services.rbac import assert_workspace_access, check_workspace_permission
from app.services.alert_center import workspace_alert_coverage
from app.services.alert_notification import (
    notify_alert_event,
    serialize_alert_notification_config,
    upsert_alert_notification_config,
)
from app.services.alert_oncall import on_call_label, parse_hhmm, parse_weekdays, shift_covers
from app.services.run_collector import collector_health

router = APIRouter(prefix="/alerts", tags=["alerts"])
_log = logging.getLogger(__name__)


def _business_date_from_dedupe_key(dedupe_key: Optional[str]) -> Optional[str]:
    """基线去重键形如 sla:workflow:12:biz:2026-09-10。"""
    m = re.search(r":biz:(\d{4}-\d{2}-\d{2})$", str(dedupe_key or ""))
    return m.group(1) if m else None


@router.get("")
def list_alerts(
    workspace_id: int,
    status: Optional[str] = Query("open"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_all_workspaces: bool = Query(False, description="平台管理员：跨工作空间查看"),
    q: Optional[str] = Query(None, description="工作流名称"),
    notification_status: Optional[str] = Query(None, description="通知状态 sent/skipped/failed/pending/partial"),
    after_armed: bool = Query(True, description="默认隐藏推送起点之前、且未实际推送的历史入库"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    告警列表。只读告警表，**不要**在这里同步调执行引擎采集：
    页面在轮询这个接口，引擎一慢就把请求和 DB 会话一起挂住，告警中心反而先打不开。
    采集是后台 scheduler_instance_poll 的事。
    """
    if include_all_workspaces:
        if not is_platform_admin(current_user):
            raise HTTPException(status_code=403, detail="仅平台管理员可查看全部工作空间")
    else:
        assert_workspace_access(db, current_user, workspace_id)
    stmt = db.query(AlertEvent)
    if not include_all_workspaces:
        stmt = stmt.filter(AlertEvent.workspace_id == workspace_id)
    if status:
        stmt = stmt.filter(AlertEvent.status == status)
    keyword = (q or "").strip()
    if keyword:
        stmt = stmt.filter(
            AlertEvent.workflow_id.in_(
                db.query(Workflow.id).filter(Workflow.name.ilike(f"%{keyword}%"))
            )
        )
    notify_status = (notification_status or "").strip()
    if notify_status:
        stmt = stmt.filter(AlertEvent.notification_status == notify_status)
    if after_armed:
        armed_at = (
            db.query(AlertNotificationConfig.notify_armed_at)
            .filter(AlertNotificationConfig.workspace_id == AlertEvent.workspace_id)
            .correlate(AlertEvent)
            .scalar_subquery()
        )
        inst_occurred = (
            db.query(func.coalesce(WorkflowInstance.finished_at, WorkflowInstance.started_at))
            .filter(WorkflowInstance.id == AlertEvent.workflow_instance_id)
            .correlate(AlertEvent)
            .scalar_subquery()
        )
        occurred = func.coalesce(inst_occurred, AlertEvent.created_at)
        stmt = stmt.filter(
            or_(
                armed_at.is_(None),
                occurred >= armed_at,
                AlertEvent.notification_status.in_(("sent", "partial")),
            )
        )
    total = stmt.count()
    rows = stmt.order_by(AlertEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    workspace_names = {
        ws.id: ws.name
        for ws in db.query(Workspace).filter(Workspace.id.in_([r.workspace_id for r in rows if r.workspace_id])).all()
    }
    workflow_names = {
        wf.id: wf.name
        for wf in db.query(Workflow).filter(Workflow.id.in_([r.workflow_id for r in rows if r.workflow_id])).all()
    }
    workflow_instances = {
        inst.id: inst
        for inst in db.query(WorkflowInstance)
        .filter(WorkflowInstance.id.in_([r.workflow_instance_id for r in rows if r.workflow_instance_id]))
        .all()
    }
    node_instances = {
        ni.id: ni
        for ni in db.query(NodeInstance)
        .filter(NodeInstance.id.in_([r.node_instance_id for r in rows if r.node_instance_id]))
        .all()
    }
    task_nodes = {
        node.id: node
        for node in db.query(TaskNode)
        .filter(TaskNode.id.in_([ni.node_id for ni in node_instances.values() if ni.node_id]))
        .all()
    }
    items = []
    for r in rows:
        wf_inst = workflow_instances.get(r.workflow_instance_id)
        node_inst = node_instances.get(r.node_instance_id)
        node = task_nodes.get(node_inst.node_id) if node_inst else None
        occurred_at = (
            getattr(node_inst, "finished_at", None)
            or getattr(wf_inst, "finished_at", None)
            or getattr(node_inst, "started_at", None)
            or getattr(wf_inst, "started_at", None)
            or r.created_at
        )
        log_summary = ""
        if node_inst and node_inst.log_content:
            log_summary = str(node_inst.log_content).strip().splitlines()[0][:300]
        items.append({
            "id": r.id,
            "workspace_id": r.workspace_id,
            "workspace_name": workspace_names.get(r.workspace_id),
            "workflow_id": r.workflow_id,
            "workflow_name": workflow_names.get(r.workflow_id),
            "workflow_instance_id": r.workflow_instance_id,
            "workflow_instance_status": getattr(wf_inst, "status", None),
            # 基线告警没有实例可挂，业务日期只存在去重键里，取出来诊断才能定到正确的那天
            "business_date": getattr(wf_inst, "business_date", None) or _business_date_from_dedupe_key(r.dedupe_key),
            "trigger_type": getattr(wf_inst, "trigger_type", None),
            "scheduler_instance_id": getattr(wf_inst, "scheduler_instance_id", None),
            "node_instance_id": r.node_instance_id,
            "node_instance_status": getattr(node_inst, "status", None),
            "node_name": getattr(node, "name", None),
            "node_type": getattr(node, "node_type", None),
            "scheduler_task_instance_id": getattr(node_inst, "scheduler_task_instance_id", None),
            "scheduler_task_code": getattr(node_inst, "scheduler_task_code", None),
            "log_summary": log_summary,
            "alert_type": r.alert_type,
            "level": r.level,
            "severity": getattr(r, "severity", None) or r.level,
            "dedupe_key": getattr(r, "dedupe_key", None),
            "assignee_id": getattr(r, "assignee_id", None),
            "assignee_group": getattr(r, "assignee_group", None),
            "notification_status": getattr(r, "notification_status", None),
            "notify_attempts": getattr(r, "notify_attempts", None),
            "notify_pending_channels": getattr(r, "notify_pending_channels", None),
            "notify_next_retry_at": getattr(r, "notify_next_retry_at", None),
            "notify_last_error": getattr(r, "notify_last_error", None),
            "status": r.status,
            "message": r.message,
            "occurred_at": occurred_at,
            "created_at": r.created_at,
            "ack_by": r.ack_by,
            "ack_at": r.ack_at,
            "resolved_at": r.resolved_at,
        })
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "after_armed": after_armed,
        "coverage": workspace_alert_coverage(db, workspace_id),
        "collector": collector_health(db),
    }


@router.post("/{alert_id}/ack")
def ack_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    row.status = "acknowledged"
    row.ack_by = current_user.id
    row.ack_at = datetime.utcnow()
    db.commit()
    return {"message": "已确认告警", "id": row.id}


@router.post("/{alert_id}/resolve")
def resolve_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    row.status = "resolved"
    row.resolved_at = datetime.utcnow()
    db.commit()
    return {"message": "已解决告警", "id": row.id}


@router.post("/{alert_id}/assign")
def assign_alert(
    alert_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    data = payload or {}
    row.assignee_id = data.get("assignee_id")
    row.assignee_group = data.get("assignee_group")
    db.commit()
    return {"message": "已指派告警", "id": row.id}


@router.post("/{alert_id}/notify")
def notify_alert(
    alert_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(AlertEvent).filter(AlertEvent.id == alert_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="告警不存在")
    if row.workspace_id:
        assert_workspace_access(db, current_user, row.workspace_id)
    data = payload or {}
    result = notify_alert_event(db, row, force=bool(data.get("force", True)))
    db.commit()
    return {"message": "已发送告警通知", "id": row.id, "notification_status": row.notification_status, **result}


def _serialize_shift(shift: AlertOnCallShift, username: str = "", on_call_now: bool = False) -> dict:
    return {
        "id": shift.id,
        "user_id": shift.user_id,
        "username": username,
        "weekdays": shift.weekdays or "*",
        "start_time": shift.start_time or "00:00",
        "end_time": shift.end_time or "24:00",
        "enabled": bool(shift.enabled),
        "note": shift.note,
        "on_call_now": on_call_now,
    }


@router.get("/oncall/shifts")
def list_oncall_shifts(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """值班表。on_call_now 标出此刻在班的班次，排错时一眼看出配的时段对不对。"""
    assert_workspace_access(db, current_user, workspace_id)
    shifts = (
        db.query(AlertOnCallShift)
        .filter(AlertOnCallShift.workspace_id == workspace_id)
        .order_by(AlertOnCallShift.id.asc())
        .all()
    )
    usernames = {}
    if shifts:
        ids = [s.user_id for s in shifts]
        usernames = {u.id: u.username for u in db.query(User).filter(User.id.in_(ids)).all()}
    local_now = _local_now(db, workspace_id)
    return {
        "items": [
            _serialize_shift(s, usernames.get(s.user_id, ""), shift_covers(s, local_now))
            for s in shifts
        ],
        "on_call_now": on_call_label(db, workspace_id),
        "local_now": local_now.strftime("%Y-%m-%d %H:%M"),
    }


def _local_now(db: Session, workspace_id: int) -> datetime:
    from app.services.alert_oncall import workspace_tz

    return datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(workspace_tz(db, workspace_id))


def _validated_shift_payload(db: Session, workspace_id: int, data: dict) -> dict:
    user_id = data.get("user_id")
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="请选择值班人")
    member = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        .first()
    )
    if not member:
        raise HTTPException(status_code=400, detail="值班人必须是本工作空间成员")
    start = str(data.get("start_time") or "00:00").strip()
    end = str(data.get("end_time") or "24:00").strip()
    if parse_hhmm(start) is None or parse_hhmm(end) is None:
        raise HTTPException(status_code=400, detail="值班起止时间须为 HH:MM")
    if start == end:
        raise HTTPException(status_code=400, detail="值班起止时间不能相同")
    weekdays = str(data.get("weekdays") or "*").strip() or "*"
    if weekdays != "*":
        parsed = parse_weekdays(weekdays)
        if not parsed:
            raise HTTPException(status_code=400, detail="星期须为 * 或 1-7 的逗号分隔值（1=周一）")
        weekdays = ",".join(str(d) for d in sorted(parsed))
    return {
        "user_id": user_id,
        "weekdays": weekdays,
        "start_time": start,
        "end_time": end,
        "enabled": bool(data.get("enabled", True)),
        "note": (str(data.get("note") or "").strip() or None),
    }


@router.post("/oncall/shifts")
def create_oncall_shift(
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    fields = _validated_shift_payload(db, workspace_id, payload or {})
    shift = AlertOnCallShift(workspace_id=workspace_id, **fields)
    db.add(shift)
    db.commit()
    db.refresh(shift)
    user = db.query(User).filter(User.id == shift.user_id).first()
    return _serialize_shift(shift, getattr(user, "username", ""), shift_covers(shift, _local_now(db, workspace_id)))


@router.put("/oncall/shifts/{shift_id}")
def update_oncall_shift(
    shift_id: int,
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    shift = db.query(AlertOnCallShift).filter(
        AlertOnCallShift.id == shift_id,
        AlertOnCallShift.workspace_id == workspace_id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="值班班次不存在")
    for key, val in _validated_shift_payload(db, workspace_id, payload or {}).items():
        setattr(shift, key, val)
    db.commit()
    db.refresh(shift)
    user = db.query(User).filter(User.id == shift.user_id).first()
    return _serialize_shift(shift, getattr(user, "username", ""), shift_covers(shift, _local_now(db, workspace_id)))


@router.delete("/oncall/shifts/{shift_id}")
def delete_oncall_shift(
    shift_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    shift = db.query(AlertOnCallShift).filter(
        AlertOnCallShift.id == shift_id,
        AlertOnCallShift.workspace_id == workspace_id,
    ).first()
    if not shift:
        raise HTTPException(status_code=404, detail="值班班次不存在")
    db.delete(shift)
    db.commit()
    return {"message": "已删除值班班次", "id": shift_id}


def _serialize_sla_rule(rule: WorkflowSlaRule, workflow_name: str = "") -> dict:
    return {
        "id": rule.id,
        "workflow_id": rule.workflow_id,
        "workflow_name": workflow_name,
        "enabled": bool(rule.enabled),
        "expect_finish_time": rule.expect_finish_time,
        "expect_finish_offset_days": rule.expect_finish_offset_days,
        "max_duration_minutes": rule.max_duration_minutes,
        "level": rule.level,
        "updated_at": rule.updated_at,
    }


@router.get("/sla/rules")
def list_sla_rules(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """基线列表：已发布工作流都列出来，没配基线的返回 rule=null 以便前端直接编辑。"""
    assert_workspace_access(db, current_user, workspace_id)
    workflows = (
        db.query(Workflow)
        .filter(Workflow.workspace_id == workspace_id, Workflow.status == "published")
        .order_by(Workflow.name.asc())
        .all()
    )
    rules = {
        r.workflow_id: r
        for r in db.query(WorkflowSlaRule).filter(WorkflowSlaRule.workspace_id == workspace_id).all()
    }
    items = []
    for wf in workflows:
        rule = rules.get(wf.id)
        items.append({
            "workflow_id": wf.id,
            "workflow_name": wf.name,
            "rule": _serialize_sla_rule(rule, wf.name) if rule else None,
        })
    return {"items": items, "covered": sum(1 for i in items if i["rule"]), "total": len(items)}


@router.put("/sla/rules/{workflow_id}")
def upsert_sla_rule(
    workflow_id: int,
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """配置某个工作流的基线：承诺完成时间与最长运行时长。"""
    check_workspace_permission(db, current_user, workspace_id, "developer")
    wf = (
        db.query(Workflow)
        .filter(Workflow.id == workflow_id, Workflow.workspace_id == workspace_id)
        .first()
    )
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    data = payload or {}
    expect = (data.get("expect_finish_time") or "").strip() or None
    if expect and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", expect):
        raise HTTPException(status_code=400, detail="承诺完成时间格式应为 HH:MM，例如 09:30")
    max_minutes = data.get("max_duration_minutes")
    if max_minutes is not None and str(max_minutes).strip() != "":
        try:
            max_minutes = int(max_minutes)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="最长运行时长须为整数分钟")
        if max_minutes <= 0:
            raise HTTPException(status_code=400, detail="最长运行时长须大于 0")
    else:
        max_minutes = None
    if not expect and max_minutes is None:
        raise HTTPException(status_code=400, detail="至少配置承诺完成时间或最长运行时长")

    rule = db.query(WorkflowSlaRule).filter(WorkflowSlaRule.workflow_id == workflow_id).first()
    if rule is None:
        # workflow_id 上有唯一约束，两个人同时配同一条基线会让先查再插的那一方 500
        from app.services.db_idempotent import insert_or_get

        rule, _created = insert_or_get(
            db,
            WorkflowSlaRule(workspace_id=workspace_id, workflow_id=workflow_id),
            lambda: (
                db.query(WorkflowSlaRule)
                .filter(WorkflowSlaRule.workflow_id == workflow_id)
                .first()
            ),
        )
    rule.workspace_id = workspace_id
    rule.enabled = bool(data.get("enabled", True))
    rule.expect_finish_time = expect
    rule.expect_finish_offset_days = int(data.get("expect_finish_offset_days", 1) or 0)
    rule.max_duration_minutes = max_minutes
    rule.level = data.get("level") or "error"
    rule.updated_by = getattr(current_user, "id", None)
    db.commit()
    db.refresh(rule)
    return _serialize_sla_rule(rule, wf.name)


@router.delete("/sla/rules/{workflow_id}")
def delete_sla_rule(
    workflow_id: int,
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "developer")
    rule = (
        db.query(WorkflowSlaRule)
        .filter(WorkflowSlaRule.workflow_id == workflow_id, WorkflowSlaRule.workspace_id == workspace_id)
        .first()
    )
    if rule is None:
        return {"message": "未配置基线"}
    db.delete(rule)
    db.commit()
    return {"message": "已删除基线"}


@router.get("/notification/config")
def get_notification_config(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    assert_workspace_access(db, current_user, workspace_id)
    cfg = db.query(AlertNotificationConfig).filter(AlertNotificationConfig.workspace_id == workspace_id).first()
    body = serialize_alert_notification_config(cfg)
    body["coverage"] = workspace_alert_coverage(db, workspace_id)
    return body


@router.put("/notification/config")
def put_notification_config(
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    try:
        cfg = upsert_alert_notification_config(db, workspace_id, payload or {}, getattr(current_user, "id", None))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    db.refresh(cfg)
    body = serialize_alert_notification_config(cfg)
    body["coverage"] = workspace_alert_coverage(db, workspace_id)
    return body


@router.post("/notification/test")
def test_notification_config(
    workspace_id: int,
    payload: Optional[dict] = Body(default=None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    check_workspace_permission(db, current_user, workspace_id, "admin")
    data = payload or {}
    try:
        cfg = upsert_alert_notification_config(db, workspace_id, data, getattr(current_user, "id", None))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    event = AlertEvent(
        workspace_id=workspace_id,
        alert_type="test",
        level="info",
        severity="info",
        status="open",
        message="这是一条 GIDO 告警通知测试消息。",
        notification_status="pending",
    )
    db.add(event)
    db.flush()
    result = notify_alert_event(db, event, force=True)
    coverage = workspace_alert_coverage(db, workspace_id)
    db.rollback()
    return {
        "message": "测试完成",
        "notification_status": event.notification_status,
        "coverage": coverage,
        **result,
    }

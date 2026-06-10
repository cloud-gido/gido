# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""发布审批：普通开发提交，空间/平台管理员审批后执行发布。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.data_service import DataApi
from app.models.workspace import PublishApproval, TaskNode, User, Workflow
from app.services.rbac import (
    assert_workspace_access,
    check_workspace_permission,
    workspace_data_full_control,
)


VALID_RESOURCE_TYPES = frozenset({"workflow", "studio_node", "stream_job", "data_service_api"})
VALID_ACTIONS = frozenset({"publish_to_ds", "publish_node", "submit_job", "publish_api", "offline_api"})
TERMINAL_STATUSES = frozenset({"approved", "rejected", "cancelled"})

_RESOURCE_ACTIONS: dict[str, frozenset[str]] = {
    "workflow": frozenset({"publish_to_ds"}),
    "studio_node": frozenset({"publish_node"}),
    "stream_job": frozenset({"submit_job"}),
    "data_service_api": frozenset({"publish_api", "offline_api"}),
}


def _perm_for_resource(resource_type: str) -> str:
    from app.core import perm_codes as PC

    return {
        "workflow": PC.GIDO_BATCH_WORKFLOW_RUN,
        "studio_node": PC.GIDO_BATCH_STUDIO_RUN,
        "stream_job": PC.GIDO_STREAM_RUN,
        "data_service_api": PC.GIDO_SERVICE_RUN,
    }[resource_type]


def _assert_approval_workspace_access(db: Session, user, workspace_id: int, resource_type: Optional[str] = None) -> None:
    """查看/提交审批：空间内 developer + 任一发布相关平台权限；管理员仅需进入空间。"""
    from app.core.access import assert_any_permission
    from app.core import perm_codes as PC

    if workspace_data_full_control(db, user, workspace_id):
        assert_workspace_access(db, user, workspace_id)
        return
    check_workspace_permission(db, user, workspace_id, "developer")
    if resource_type:
        from app.core.access import user_has_any

        if user_has_any(db, user, [_perm_for_resource(resource_type)]):
            return
        raise HTTPException(status_code=403, detail="无权提交该类型发布审批")
    assert_any_permission(
        db,
        user,
        PC.GIDO_BATCH_OPERATION_READ,
        PC.GIDO_BATCH_WORKFLOW_RUN,
        PC.GIDO_BATCH_STUDIO_RUN,
        PC.GIDO_STREAM_RUN,
        PC.GIDO_SERVICE_RUN,
    )


def _user_name(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    return u.username if u else None


def _resolve_resource(
    db: Session, workspace_id: int, resource_type: str, resource_id: int
) -> tuple[str, Any]:
    if resource_type == "workflow":
        wf = db.query(Workflow).filter(Workflow.id == resource_id, Workflow.workspace_id == workspace_id).first()
        if not wf:
            raise HTTPException(status_code=404, detail="工作流不存在")
        return wf.name, wf
    if resource_type == "studio_node":
        node = db.query(TaskNode).filter(TaskNode.id == resource_id, TaskNode.workspace_id == workspace_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="开发节点不存在")
        return node.name, node
    if resource_type == "stream_job":
        from app.api.streaming import StreamingJob

        job = db.query(StreamingJob).filter(StreamingJob.id == resource_id, StreamingJob.workspace_id == workspace_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="实时作业不存在")
        return job.name, job
    if resource_type == "data_service_api":
        api = db.query(DataApi).filter(DataApi.id == resource_id, DataApi.workspace_id == workspace_id).first()
        if not api:
            raise HTTPException(status_code=404, detail="数据服务 API 不存在")
        label = f"{api.name} ({api.api_code})"
        return label, api
    raise HTTPException(status_code=400, detail=f"不支持的资源类型: {resource_type}")


def serialize_approval(db: Session, row: PublishApproval) -> Dict[str, Any]:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "resource_name": row.resource_name,
        "action": row.action,
        "status": row.status,
        "submit_note": row.submit_note,
        "review_note": row.review_note,
        "submitted_by": row.submitted_by,
        "submitted_by_username": _user_name(db, row.submitted_by),
        "reviewed_by": row.reviewed_by,
        "reviewed_by_username": _user_name(db, row.reviewed_by),
        "submitted_at": row.submitted_at,
        "reviewed_at": row.reviewed_at,
    }


def assert_can_publish_production(db: Session, user, workspace_id: int) -> None:
    """直接发布到生产：仅空间管理员或平台管理员。"""
    if not workspace_data_full_control(db, user, workspace_id):
        raise HTTPException(
            status_code=403,
            detail="仅空间管理员或平台管理员可直接发布到生产；普通开发请提交审批",
        )


def find_pending_approval(
    db: Session, workspace_id: int, resource_type: str, resource_id: int, action: str
) -> Optional[PublishApproval]:
    return (
        db.query(PublishApproval)
        .filter(
            PublishApproval.workspace_id == workspace_id,
            PublishApproval.resource_type == resource_type,
            PublishApproval.resource_id == resource_id,
            PublishApproval.action == action,
            PublishApproval.status == "pending",
        )
        .first()
    )


def submit_publish_approval(
    db: Session,
    user,
    workspace_id: int,
    resource_type: str,
    resource_id: int,
    action: str,
    submit_note: Optional[str] = None,
) -> PublishApproval:
    if resource_type not in VALID_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的资源类型: {resource_type}")
    if action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的发布动作: {action}")
    allowed = _RESOURCE_ACTIONS.get(resource_type, frozenset())
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"{resource_type} 不支持动作 {action}")

    _assert_approval_workspace_access(db, user, workspace_id, resource_type)

    if workspace_data_full_control(db, user, workspace_id):
        raise HTTPException(status_code=400, detail="管理员可直接发布，无需提交审批")

    name, resource = _resolve_resource(db, workspace_id, resource_type, resource_id)
    if resource_type == "data_service_api":
        if action == "publish_api" and getattr(resource, "status", None) == "online":
            raise HTTPException(status_code=400, detail="API 已上线，无需重复发布")
        if action == "offline_api" and getattr(resource, "status", None) != "online":
            raise HTTPException(status_code=400, detail="仅已上线 API 可提交下线审批")

    existing = find_pending_approval(db, workspace_id, resource_type, resource_id, action)
    if existing:
        raise HTTPException(status_code=409, detail="已有待审批申请，请等待管理员处理")

    row = PublishApproval(
        workspace_id=workspace_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=name,
        action=action,
        status="pending",
        submit_note=(submit_note or "").strip() or None,
        submitted_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _execute_approval_action(db: Session, row: PublishApproval, reviewer) -> Dict[str, Any]:
    if row.action == "publish_to_ds":
        from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
        from app.services.workflow_ds_publish import publish_workflow_to_ds

        wf = db.query(Workflow).filter(Workflow.id == row.resource_id).first()
        if not wf:
            raise HTTPException(status_code=404, detail="工作流不存在")
        if not get_dolphin_runtime(db, wf.workspace_id).enabled:
            raise HTTPException(status_code=400, detail="DolphinScheduler 未启用")
        refresh_ds_client(db, wf.workspace_id)
        return publish_workflow_to_ds(db, wf)

    if row.action == "publish_node":
        node = db.query(TaskNode).filter(TaskNode.id == row.resource_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="开发节点不存在")
        node.is_published = True
        if settings.STUDIO_LOCK_ON_PUBLISH:
            node.is_locked = True
        node.updated_at = datetime.utcnow()
        db.commit()
        return {"message": "节点已发布"}

    if row.action == "submit_job":
        from app.api.streaming import StreamingJob, execute_streaming_job_submit

        job = db.query(StreamingJob).filter(StreamingJob.id == row.resource_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="实时作业不存在")
        return execute_streaming_job_submit(db, job, reviewer, script_content=None)

    if row.action == "publish_api":
        from app.services.data_service_publish import execute_data_api_publish

        api = db.query(DataApi).filter(DataApi.id == row.resource_id).first()
        if not api:
            raise HTTPException(status_code=404, detail="数据服务 API 不存在")
        return execute_data_api_publish(db, api, reviewer)

    if row.action == "offline_api":
        from app.services.data_service_publish import execute_data_api_offline

        api = db.query(DataApi).filter(DataApi.id == row.resource_id).first()
        if not api:
            raise HTTPException(status_code=404, detail="数据服务 API 不存在")
        return execute_data_api_offline(db, api)

    raise HTTPException(status_code=400, detail=f"未知动作: {row.action}")


def approve_publish_approval(
    db: Session, user, approval_id: int, review_note: Optional[str] = None
) -> Dict[str, Any]:
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    assert_can_publish_production(db, user, row.workspace_id)
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态不可审批: {row.status}")

    try:
        exec_out = _execute_approval_action(db, row, user)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"发布执行失败: {e}")

    row.status = "approved"
    row.review_note = (review_note or "").strip() or None
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    if row.resource_type == "workflow":
        wf = db.query(Workflow).filter(Workflow.id == row.resource_id).first()
        if wf:
            wf.updated_by = user.id
            wf.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"approval": serialize_approval(db, row), "result": exec_out}


def reject_publish_approval(
    db: Session, user, approval_id: int, review_note: Optional[str] = None
) -> PublishApproval:
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    assert_can_publish_production(db, user, row.workspace_id)
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态不可驳回: {row.status}")
    row.status = "rejected"
    row.review_note = (review_note or "").strip() or None
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def cancel_publish_approval(db: Session, user, approval_id: int) -> PublishApproval:
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    if row.submitted_by != user.id and not workspace_data_full_control(db, user, row.workspace_id):
        raise HTTPException(status_code=403, detail="仅提交人或管理员可撤回")
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态不可撤回: {row.status}")
    row.status = "cancelled"
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def list_publish_approvals(
    db: Session,
    user,
    workspace_id: int,
    status: Optional[str] = None,
    mine_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    _assert_approval_workspace_access(db, user, workspace_id)
    q = db.query(PublishApproval).filter(PublishApproval.workspace_id == workspace_id)
    if status:
        q = q.filter(PublishApproval.status == status)
    if mine_only and not workspace_data_full_control(db, user, workspace_id):
        q = q.filter(PublishApproval.submitted_by == user.id)
    total = q.count()
    rows = (
        q.order_by(PublishApproval.submitted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [serialize_approval(db, r) for r in rows],
        "can_review": workspace_data_full_control(db, user, workspace_id),
    }


def pending_approval_count(db: Session, workspace_id: int) -> int:
    return (
        db.query(PublishApproval)
        .filter(PublishApproval.workspace_id == workspace_id, PublishApproval.status == "pending")
        .count()
    )


def pending_resource_keys(db: Session, workspace_id: int) -> List[str]:
    rows = (
        db.query(PublishApproval)
        .filter(PublishApproval.workspace_id == workspace_id, PublishApproval.status == "pending")
        .all()
    )
    return [f"{r.resource_type}:{r.resource_id}:{r.action}" for r in rows]

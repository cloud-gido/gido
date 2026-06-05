# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.services.publish_approval import (
    approve_publish_approval,
    cancel_publish_approval,
    list_publish_approvals,
    pending_approval_count,
    reject_publish_approval,
    serialize_approval,
    submit_publish_approval,
)

router = APIRouter(prefix="/approvals", tags=["发布审批"])


class ApprovalSubmitIn(BaseModel):
    workspace_id: int
    resource_type: str  # workflow | studio_node
    resource_id: int
    action: str  # publish_to_ds | publish_node
    submit_note: Optional[str] = None


class ApprovalReviewIn(BaseModel):
    review_note: Optional[str] = None


@router.post("/submit")
def submit_approval(body: ApprovalSubmitIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = submit_publish_approval(
        db,
        current_user,
        body.workspace_id,
        body.resource_type,
        body.resource_id,
        body.action,
        body.submit_note,
    )
    return {"message": "已提交审批，等待空间/平台管理员处理", "approval": serialize_approval(db, row)}


@router.get("")
def list_approvals(
    workspace_id: int,
    status: Optional[str] = Query(None, description="pending/approved/rejected/cancelled"),
    mine_only: bool = Query(False, description="非管理员时仅看自己的申请"),
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return list_publish_approvals(db, current_user, workspace_id, status, mine_only, page, page_size)


@router.get("/pending-count")
def get_pending_count(
    workspace_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = list_publish_approvals(db, current_user, workspace_id, status="pending", page=1, page_size=1)
    return {"count": pending_approval_count(db, workspace_id), "can_review": data["can_review"]}


@router.post("/{approval_id}/approve")
def approve_approval(
    approval_id: int,
    body: ApprovalReviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    out = approve_publish_approval(db, current_user, approval_id, body.review_note)
    return {"message": "审批通过并已发布到生产", **out}


@router.post("/{approval_id}/reject")
def reject_approval(
    approval_id: int,
    body: ApprovalReviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = reject_publish_approval(db, current_user, approval_id, body.review_note)
    return {"message": "已驳回", "approval": serialize_approval(db, row)}


@router.post("/{approval_id}/cancel")
def cancel_approval(
    approval_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = cancel_publish_approval(db, current_user, approval_id)
    return {"message": "已撤回", "approval": serialize_approval(db, row)}

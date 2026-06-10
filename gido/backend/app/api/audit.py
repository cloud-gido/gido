# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, Set
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.core.access import user_has_any, is_platform_manager_role
from app.models.workspace import AuditLog, User
from app.services.rbac import get_accessible_workspace_ids, workspace_ids_where_user_space_admin

router = APIRouter(prefix="/audit", tags=["审计日志"])


def _audit_viewable_workspace_ids(db: Session, user) -> tuple[bool, Set[int]]:
    """
    Returns (platform_unrestricted, workspace_id_set)。
    platform_unrestricted 为 True 时不过滤 workspace（平台管理员）。
    否则为有权限查看审计日志的工作空间 id 集合（成员+audit 与 空间管理员 并集）。
    """
    if is_platform_manager_role(db, user):
        return True, set()
    allowed: Set[int] = set()
    if user_has_any(db, user, [PC.AUDIT_READ]):
        allowed.update(get_accessible_workspace_ids(db, user))
    allowed.update(workspace_ids_where_user_space_admin(db, user))
    return False, allowed


@router.get("/logs")
def list_audit_logs(
    workspace_id: Optional[int] = None,
    resource_type: Optional[str] = None,
    action: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    unrestricted, allowed_ids = _audit_viewable_workspace_ids(db, current_user)

    q = db.query(AuditLog)
    if unrestricted:
        if workspace_id is not None:
            q = q.filter(AuditLog.workspace_id == workspace_id)
    else:
        if not allowed_ids:
            return {"total": 0, "page": page, "page_size": page_size, "items": []}
        if workspace_id is not None:
            if workspace_id not in allowed_ids:
                raise HTTPException(status_code=403, detail="无权查看该工作空间的审计日志")
            q = q.filter(AuditLog.workspace_id == workspace_id)
        else:
            q = q.filter(AuditLog.workspace_id.in_(allowed_ids))

    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    logs = q.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    result = []
    for log in logs:
        actor = db.query(User).filter(User.id == log.user_id).first()
        result.append({
            "id": log.id,
            "username": actor.username if actor else "",
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_name": log.resource_name,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": log.created_at,
        })
    return {"total": total, "page": page, "page_size": page_size, "items": result}

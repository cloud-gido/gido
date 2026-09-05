# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""默认工作空间 infras：成员归属、前端默认选中解析。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.access import is_platform_manager_role
from app.models.rbac_models import Role
from app.models.workspace import Workspace, WorkspaceMember, User
from app.services.rbac import VALID_SPACE_MEMBER_ROLES, get_accessible_workspace_ids

DEFAULT_WORKSPACE_NAME = "infras"

# 平台角色 → 自动加入 infras 时的空间成员档位（自定义角色默认只读）
_PLATFORM_CODE_TO_SPACE_ROLE = {
    "super_admin": "admin",
    "platform_admin": "admin",
    "developer": "developer",
    "operator": "developer",
    "workspace_steward": "developer",
    "analyst": "viewer",
}


def get_default_workspace(db: Session) -> Optional[Workspace]:
    return db.query(Workspace).filter(Workspace.name == DEFAULT_WORKSPACE_NAME).first()


def space_role_for_platform_user(db: Session, user: User) -> str:
    """按平台角色推导默认空间成员角色；未知/自定义 → viewer。"""
    code = None
    linked = getattr(user, "system_role", None)
    if linked is not None and getattr(linked, "code", None):
        code = linked.code
    elif user.role_id:
        row = db.query(Role).filter(Role.id == user.role_id).first()
        code = row.code if row else None
    role = _PLATFORM_CODE_TO_SPACE_ROLE.get(code or "", "viewer")
    return role if role in VALID_SPACE_MEMBER_ROLES else "viewer"


def ensure_default_workspace_membership(
    db: Session,
    user: User,
    *,
    member_role: Optional[str] = None,
) -> Dict[str, Any]:
    """
    将用户加入默认空间 infras（若尚无成员或负责人关系）。
    member_role 未指定时按平台角色映射（分析师→viewer，开发/运维→developer，平台管理→admin）。
    返回 {workspace_id, workspace_name, member_role, created}。
    """
    ws = get_default_workspace(db)
    if not ws:
        return {
            "workspace_id": None,
            "workspace_name": DEFAULT_WORKSPACE_NAME,
            "member_role": None,
            "created": False,
        }
    if ws.owner_id == user.id:
        return {
            "workspace_id": ws.id,
            "workspace_name": ws.name,
            "member_role": "admin",
            "created": False,
        }
    existing = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
        .first()
    )
    if existing:
        return {
            "workspace_id": ws.id,
            "workspace_name": ws.name,
            "member_role": existing.role,
            "created": False,
        }
    role = (member_role or "").strip().lower() if member_role else space_role_for_platform_user(db, user)
    if role not in VALID_SPACE_MEMBER_ROLES:
        role = "viewer"
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=role))
    db.commit()
    return {
        "workspace_id": ws.id,
        "workspace_name": ws.name,
        "member_role": role,
        "created": True,
    }


def resolve_default_workspace_id(db: Session, user: User) -> Optional[int]:
    """
    登录用户「默认应选中」的工作空间 id：优先可访问的 infras，否则取可访问空间中 id 最小者（稳定）。
    平台管理员优先指向 infras（若存在）。
    """
    ws_def = get_default_workspace(db)
    if is_platform_manager_role(db, user):
        if ws_def:
            return ws_def.id
        first = db.query(Workspace).order_by(Workspace.id).first()
        return first.id if first else None

    accessible = set(get_accessible_workspace_ids(db, user))
    if not accessible:
        return None
    if ws_def and ws_def.id in accessible:
        return ws_def.id
    return min(accessible)


def backfill_all_users_default_workspace(db: Session, *, member_role: Optional[str] = None) -> int:
    """
    启动/迁移：为尚未加入 infras 的用户补成员行。
    member_role 若传入则全员同一档；否则按各用户平台角色映射。
    """
    ws = get_default_workspace(db)
    if not ws:
        return 0
    added = 0
    for u in db.query(User).all():
        if ws.owner_id == u.id:
            continue
        exists = (
            db.query(WorkspaceMember)
            .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == u.id)
            .first()
        )
        if exists:
            continue
        role = (member_role or "").strip().lower() if member_role else space_role_for_platform_user(db, u)
        if role not in VALID_SPACE_MEMBER_ROLES:
            role = "viewer"
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role=role))
        added += 1
    if added:
        db.commit()
    return added

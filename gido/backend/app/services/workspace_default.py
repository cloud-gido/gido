# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""默认工作空间 infras：成员归属、前端默认选中解析。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.core.access import is_platform_manager_role
from app.models.workspace import Workspace, WorkspaceMember, User
from app.services.rbac import get_accessible_workspace_ids

DEFAULT_WORKSPACE_NAME = "infras"


def get_default_workspace(db: Session) -> Optional[Workspace]:
    return db.query(Workspace).filter(Workspace.name == DEFAULT_WORKSPACE_NAME).first()


def ensure_default_workspace_membership(
    db: Session,
    user: User,
    *,
    member_role: str = "developer",
) -> Optional[int]:
    """
    将用户加入默认空间 infras（若尚无成员或负责人关系）。
    普通注册用户与管理员创建的用户应在首次写入后即归属默认空间，便于与「空间管理员」模型对齐。
    """
    ws = get_default_workspace(db)
    if not ws:
        return None
    if ws.owner_id == user.id:
        return ws.id
    existing = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id)
        .first()
    )
    if existing:
        return ws.id
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=member_role))
    db.commit()
    return ws.id


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


def backfill_all_users_default_workspace(db: Session, *, member_role: str = "developer") -> int:
    """启动/迁移时执行：为尚未加入 infras 的用户补成员行。返回新增成员行数量。"""
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
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role=member_role))
        added += 1
    if added:
        db.commit()
    return added

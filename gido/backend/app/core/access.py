# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from typing import Set, Iterable, Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User
from app.models.rbac_models import Role, Permission, role_permissions

# 平台管理能力只由平台角色授予；is_admin 列为兼容缓存，随角色自动同步。
PLATFORM_MANAGER_ROLE_CODES = frozenset({"super_admin", "platform_admin"})


def role_grants_platform_admin(role_code: Optional[str]) -> bool:
    return bool(role_code and role_code in PLATFORM_MANAGER_ROLE_CODES)


def sync_is_admin_with_role(user: User, role: Optional[Role] = None) -> None:
    """按平台角色写回 is_admin，避免 UI 出现「角色 / 管理员开关」双轨。"""
    code = role.code if role is not None else None
    if code is None:
        linked = getattr(user, "system_role", None)
        if linked is not None:
            code = linked.code
    user.is_admin = bool(role_grants_platform_admin(code) or user.username == "admin")


def is_platform_admin(user: User) -> bool:
    """
    是否具备平台管理能力（创建空间、平台集成、用户启停等）。
    真相源为平台角色 super_admin / platform_admin；is_admin 与用户名 admin 为兼容路径。
    """
    if user.is_admin is True or (user.username == "admin" and user.is_admin is not False):
        return True
    linked = getattr(user, "system_role", None)
    if linked is not None and role_grants_platform_admin(linked.code):
        return True
    return False


def get_user_permission_codes(db: Session, user: User) -> Set[str]:
    """返回用户拥有的权限码集合；平台管理员账号或 platform_admin/super_admin 角色视为平台全量。"""
    if is_platform_manager_role(db, user):
        return {"*"}
    if not user.role_id:
        return set()
    role = db.query(Role).filter(Role.id == user.role_id).first()
    if not role:
        return set()
    rows = db.execute(
        select(Permission.code).join(
            role_permissions, Permission.id == role_permissions.c.permission_id
        ).where(role_permissions.c.role_id == role.id)
    ).scalars().all()
    return set(rows)


def user_has_any(db: Session, user: User, codes: Iterable[str]) -> bool:
    owned = get_user_permission_codes(db, user)
    if "*" in owned:
        return True
    return bool(owned.intersection(set(codes)))


def assert_any_permission(db: Session, user: User, *codes: str) -> None:
    if not codes:
        return
    if user_has_any(db, user, codes):
        return
    raise HTTPException(status_code=403, detail="权限不足，需要以下权限之一: " + ", ".join(codes))


class RequireAnyPerm:
    """FastAPI 依赖：具备任一权限码即可访问。"""

    def __init__(self, *codes: str):
        self.codes = codes

    def __call__(self, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        assert_any_permission(db, user, *self.codes)


def require_admin_or_perm(*codes: str):
    """管理员(is_admin) 或 具备任一平台权限。"""
    def _dep(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
        if is_platform_admin(user):
            return
        assert_any_permission(db, user, *codes)
    return _dep


def require_system_admin(user: User = Depends(get_current_user)) -> None:
    """仅平台账号（系统管理员）；用于仍为平台专属的接口。"""
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅系统管理员可执行此操作")


def is_platform_manager_role(db: Session, user: User) -> bool:
    """是否承担「平台管理员」职责：与 is_platform_admin 一致；system_role 未加载时回查 role_id。"""
    if is_platform_admin(user):
        return True
    if not user.role_id:
        return False
    role = db.query(Role).filter(Role.id == user.role_id).first()
    return role_grants_platform_admin(role.code if role else None)


def require_platform_manager(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """
    平台级能力：创建工作空间、删除工作空间、配置 Dolphin/Flink 插拔集成等。
    与「空间管理员」（仅某一工作空间内的 admin 成员角色）区分。
    """
    if is_platform_manager_role(db, user):
        return
    raise HTTPException(status_code=403, detail="仅平台管理员可执行此操作")

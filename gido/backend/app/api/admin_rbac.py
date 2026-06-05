# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, EmailStr
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.access import RequireAnyPerm, is_platform_admin
from app.core import perm_codes as P
from app.models.workspace import User
from app.models.rbac_models import Role, Permission
from app.core.security import get_password_hash
from app.services.workspace_default import ensure_default_workspace_membership

router = APIRouter(prefix="/admin", tags=["系统管理-角色权限"])


# ---------- Schemas ----------

class PermissionOut(BaseModel):
    id: int
    code: str
    name: str
    module: str

    class Config:
        from_attributes = True


class RoleOut(BaseModel):
    id: int
    code: str
    name: str
    description: Optional[str]
    is_system: bool
    permission_codes: List[str]


class RoleCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    description: Optional[str] = None
    permission_codes: List[str] = []


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permission_codes: Optional[List[str]] = None


class UserBriefOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    is_admin: bool
    is_active: bool
    role_id: Optional[int]
    role_code: Optional[str]
    role_name: Optional[str]


class UserRoleUpdate(BaseModel):
    role_id: int


class UserAdminFlagsUpdate(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None


class AdminUserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    full_name: Optional[str] = None
    role_id: Optional[int] = None


def _role_to_out(r: Role) -> RoleOut:
    return RoleOut(
        id=r.id,
        code=r.code,
        name=r.name,
        description=r.description,
        is_system=r.is_system,
        permission_codes=[p.code for p in r.permissions],
    )


# ---------- Permissions catalog ----------

@router.get("/permissions", response_model=List[PermissionOut])
def list_all_permissions(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_ROLE_READ)),
):
    return db.query(Permission).order_by(Permission.module, Permission.code).all()


# ---------- Roles ----------

@router.get("/roles", response_model=List[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_ROLE_READ)),
):
    roles = db.query(Role).order_by(Role.id).all()
    return [_role_to_out(r) for r in roles]


@router.post("/roles", response_model=RoleOut)
def create_role(
    body: RoleCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_ROLE_WRITE)),
):
    if db.query(Role).filter(Role.code == body.code).first():
        raise HTTPException(status_code=400, detail="角色 code 已存在")
    perms = db.query(Permission).filter(Permission.code.in_(body.permission_codes)).all()
    if len(perms) != len(set(body.permission_codes)):
        raise HTTPException(status_code=400, detail="存在无效的权限码")
    role = Role(code=body.code, name=body.name, description=body.description or "", is_system=False)
    role.permissions = perms
    db.add(role)
    db.commit()
    db.refresh(role)
    return _role_to_out(role)


@router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: int,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_ROLE_WRITE)),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.is_system and body.permission_codes is not None:
        raise HTTPException(status_code=400, detail="内置角色不可修改权限集合，请新建自定义角色")
    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permission_codes is not None:
        perms = db.query(Permission).filter(Permission.code.in_(body.permission_codes)).all()
        if len(perms) != len(set(body.permission_codes)):
            raise HTTPException(status_code=400, detail="存在无效的权限码")
        role.permissions = perms
    db.commit()
    db.refresh(role)
    return _role_to_out(role)


@router.delete("/roles/{role_id}")
def delete_role(
    role_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_ROLE_DELETE)),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.is_system:
        raise HTTPException(status_code=400, detail="不能删除内置角色")
    if db.query(User).filter(User.role_id == role_id).first():
        raise HTTPException(status_code=400, detail="仍有用户绑定此角色，请先调整用户角色")
    db.delete(role)
    db.commit()
    return {"message": "已删除"}


# ---------- Users (platform) ----------

@router.post("/users", response_model=UserBriefOut)
def admin_create_user(
    body: AdminUserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_USER_WRITE)),
):
    """管理员创建用户并指定平台角色（默认 developer）。"""
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="邮箱已存在")
    role_id = body.role_id
    if role_id is not None:
        if not db.query(Role).filter(Role.id == role_id).first():
            raise HTTPException(status_code=400, detail="角色不存在")
    else:
        dev = db.query(Role).filter(Role.code == "developer").first()
        role_id = dev.id if dev else None
    u = User(
        username=body.username,
        email=body.email,
        full_name=body.full_name,
        hashed_password=get_password_hash(body.password),
        role_id=role_id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    ensure_default_workspace_membership(db, u)
    db.refresh(u)
    rc, rn = None, None
    if u.system_role:
        rc, rn = u.system_role.code, u.system_role.name
    return UserBriefOut(
        id=u.id, username=u.username, email=u.email, full_name=u.full_name,
        is_admin=u.is_admin, is_active=u.is_active, role_id=u.role_id,
        role_code=rc, role_name=rn,
    )


@router.get("/users", response_model=List[UserBriefOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    __: None = Depends(RequireAnyPerm(P.SYSTEM_USER_READ)),
):
    users = db.query(User).order_by(User.id).all()
    out: List[UserBriefOut] = []
    for u in users:
        rc, rn = None, None
        if u.system_role:
            rc, rn = u.system_role.code, u.system_role.name
        out.append(UserBriefOut(
            id=u.id, username=u.username, email=u.email, full_name=u.full_name,
            is_admin=u.is_admin, is_active=u.is_active, role_id=u.role_id,
            role_code=rc, role_name=rn,
        ))
    return out


@router.put("/users/{user_id}/role", response_model=UserBriefOut)
def set_user_role(
    user_id: int,
    body: UserRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(RequireAnyPerm(P.SYSTEM_USER_WRITE)),
):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    role = db.query(Role).filter(Role.id == body.role_id).first()
    if not role:
        raise HTTPException(status_code=400, detail="角色不存在")
    u.role_id = body.role_id
    db.commit()
    db.refresh(u)
    rc, rn = role.code, role.name
    return UserBriefOut(
        id=u.id, username=u.username, email=u.email, full_name=u.full_name,
        is_admin=u.is_admin, is_active=u.is_active, role_id=u.role_id,
        role_code=rc, role_name=rn,
    )


@router.put("/users/{user_id}/flags", response_model=UserBriefOut)
def set_user_flags(
    user_id: int,
    body: UserAdminFlagsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(RequireAnyPerm(P.SYSTEM_USER_WRITE)),
):
    if not is_platform_admin(current_user):
        raise HTTPException(status_code=403, detail="仅超级管理员可修改管理员/禁用状态")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if u.id == current_user.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="不能禁用当前登录用户")
    if body.is_admin is not None:
        u.is_admin = body.is_admin
    if body.is_active is not None:
        u.is_active = body.is_active
    db.commit()
    db.refresh(u)
    rc, rn = None, None
    if u.system_role:
        rc, rn = u.system_role.code, u.system_role.name
    return UserBriefOut(
        id=u.id, username=u.username, email=u.email, full_name=u.full_name,
        is_admin=u.is_admin, is_active=u.is_active, role_id=u.role_id,
        role_code=rc, role_name=rn,
    )


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _: None = Depends(RequireAnyPerm(P.SYSTEM_USER_DELETE)),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    if is_platform_admin(u) and not is_platform_admin(current_user):
        raise HTTPException(status_code=403, detail="无权删除管理员账号")
    db.delete(u)
    db.commit()
    return {"message": "已删除"}

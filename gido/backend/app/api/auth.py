# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any, Dict

from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token, get_current_user
from app.core.access import get_user_permission_codes, RequireAnyPerm, is_platform_admin
from app.core import perm_codes as P
from app.models.workspace import User
from app.models.rbac_models import Role
from app.services.workspace_default import ensure_default_workspace_membership, resolve_default_workspace_id
from app.services.user_avatar import (
    normalize_avatar_value,
    replace_avatar_upload,
    avatar_file_path,
    media_type_for_stored_name,
    parse_upload_stored_name,
    delete_uploaded_avatar,
)

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["认证"])


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    avatar: Optional[str] = None
    is_admin: bool
    is_active: bool = True
    role_id: Optional[int] = None
    role_code: Optional[str] = None
    role_name: Optional[str] = None
    permissions: List[str] = []
    # 登录后前端默认选中的工作空间（通常为 infras）
    default_workspace_id: Optional[int] = None


class AvatarUpdate(BaseModel):
    avatar: Optional[str] = None


def _user_payload(db: Session, user: User) -> UserOut:
    codes = get_user_permission_codes(db, user)
    perms = sorted(P.ALL_PERMISSIONS) if "*" in codes else sorted(codes)
    rc, rn = None, None
    if user.system_role:
        rc, rn = user.system_role.code, user.system_role.name
    plat_admin = is_platform_admin(user)
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        avatar=getattr(user, "avatar", None),
        is_admin=plat_admin,
        is_active=user.is_active is not False,
        role_id=user.role_id,
        role_code=rc,
        role_name=rn,
        permissions=perms,
        default_workspace_id=resolve_default_workspace_id(db, user),
    )


def _user_payload_for_login(db: Session, user: User) -> Dict[str, Any]:
    """登录成功后的用户信息；组装失败时不阻断登录（避免 RBAC/关联表异常误伤）。"""
    try:
        out = _user_payload(db, user)
        return out.model_dump() if hasattr(out, "model_dump") else dict(out)
    except Exception:
        _log.exception("登录后组装用户信息失败，使用降级数据")
        codes = get_user_permission_codes(db, user)
        perms = sorted(P.ALL_PERMISSIONS) if "*" in codes else sorted(codes)
        rc, rn = None, None
        try:
            if user.role_id and user.system_role:
                rc, rn = user.system_role.code, user.system_role.name
        except Exception:
            pass
        try:
            dw_id = resolve_default_workspace_id(db, user)
        except Exception:
            dw_id = None
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email or "",
            "full_name": user.full_name,
            "avatar": getattr(user, "avatar", None),
            "is_admin": is_platform_admin(user),
            "is_active": user.is_active is not False,
            "role_id": user.role_id,
            "role_code": rc,
            "role_name": rn,
            "permissions": perms,
            "default_workspace_id": dw_id,
        }


@router.post("/register", response_model=UserOut)
def register(
    user_in: UserCreate,
    db: Session = Depends(get_db),
    _: None = Depends(RequireAnyPerm(P.SYSTEM_USER_WRITE)),
):
    if db.query(User).filter(User.username == user_in.username).first():
        raise HTTPException(status_code=400, detail="用户名已存在")
    dev = db.query(Role).filter(Role.code == "developer").first()
    user = User(
        username=user_in.username,
        email=user_in.email,
        full_name=user_in.full_name,
        hashed_password=get_password_hash(user_in.password),
        role_id=dev.id if dev else None,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    ensure_default_workspace_membership(db, user)
    db.refresh(user)
    return _user_payload(db, user)


def _login_issue_token(db: Session, username: str, password: str) -> dict:
    username = (username or "").strip()
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.hashed_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    try:
        pwd_ok = verify_password(password, user.hashed_password)
    except Exception:
        pwd_ok = False
    if not pwd_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    # 仅当显式 is_active=False 时禁用；NULL 视为可登录（兼容加列未回填的历史数据）
    if user.is_active is False:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    token = create_access_token({"sub": user.username})
    return {"access_token": token, "token_type": "bearer", "user": _user_payload_for_login(db, user)}


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)):
    """
    登录请求体解析顺序（避免代理/客户端漏写 Content-Type 导致进错分支）：
    1) multipart/form-data
    2) 声明为 application/json 的 JSON
    3) 读取原始 body：若以 { 开头则按 JSON；否则按 x-www-form-urlencoded 解析
    """
    ct = (request.headers.get("content-type") or "").lower()

    if "multipart/form-data" in ct:
        form = await request.form()
        u, p = form.get("username"), form.get("password")
        if u is None or p is None:
            raise HTTPException(status_code=422, detail="缺少 username 或 password")
        return _login_issue_token(db, str(u), str(p))

    if "application/json" in ct:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="无效的 JSON 请求体")
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="无效的 JSON 请求体")
        u, p = body.get("username"), body.get("password")
        if u is None or p is None:
            raise HTTPException(status_code=422, detail="缺少 username 或 password")
        return _login_issue_token(db, str(u), str(p))

    raw = await request.body()
    if not raw.strip():
        raise HTTPException(status_code=422, detail="空请求体")

    if raw.lstrip().startswith(b"{"):
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=422, detail="无效的 JSON 请求体")
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="无效的 JSON 请求体")
        u, p = body.get("username"), body.get("password")
        if u is None or p is None:
            raise HTTPException(status_code=422, detail="缺少 username 或 password")
        return _login_issue_token(db, str(u), str(p))

    try:
        text = raw.decode("utf-8")
        qs = parse_qs(text, keep_blank_values=True)
        u_list, p_list = qs.get("username"), qs.get("password")
        if u_list and p_list and u_list[0] is not None and p_list[0] is not None:
            return _login_issue_token(db, str(u_list[0]), str(p_list[0]))
    except Exception:
        pass

    raise HTTPException(
        status_code=422,
        detail='无法解析登录请求，请使用 JSON: {"username":"…","password":"…"} 或表单字段 username/password',
    )


@router.get("/me", response_model=UserOut)
def get_me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _user_payload(db, current_user)


@router.patch("/me/avatar", response_model=UserOut)
def update_my_avatar(
    body: AvatarUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        new_avatar = normalize_avatar_value(body.avatar)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    old_stored = parse_upload_stored_name(getattr(current_user, "avatar", None))
    current_user.avatar = new_avatar
    db.commit()
    db.refresh(current_user)
    if old_stored and parse_upload_stored_name(new_avatar) != old_stored:
        delete_uploaded_avatar(old_stored)
    return _user_payload(db, current_user)


@router.post("/me/avatar/upload", response_model=UserOut)
async def upload_my_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    try:
        _, avatar_value = replace_avatar_upload(
            current_user.id,
            getattr(current_user, "avatar", None),
            content,
            file.content_type or "",
        )
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    current_user.avatar = avatar_value
    db.commit()
    db.refresh(current_user)
    return _user_payload(db, current_user)


@router.get("/avatars/{stored_name}")
def get_avatar_file(stored_name: str):
    path = avatar_file_path(stored_name)
    if not path:
        raise HTTPException(status_code=404, detail="头像不存在")
    return FileResponse(path, media_type=media_type_for_stored_name(stored_name))


@router.post("/change-password")
def change_password(
    body: PasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if len(body.new_password.strip()) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    current_user.hashed_password = get_password_hash(body.new_password)
    db.commit()
    return {"message": "密码已更新，请使用新密码登录"}

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from app.core.database import get_db
from app.core.security import get_current_user
from app.core.access import require_platform_manager, is_platform_manager_role
from app.models.workspace import Workspace, WorkspaceMember, User
from app.services.rbac import (
    get_user_role,
    assert_workspace_access,
    assert_can_list_workspaces,
    assert_can_manage_workspace_members,
    assert_can_edit_workspace_metadata,
    VALID_SPACE_MEMBER_ROLES,
)

router = APIRouter(prefix="/workspaces", tags=["工作空间"])


class WorkspaceCreate(BaseModel):
    name: str
    description: Optional[str] = None
    timezone: str = "Asia/Shanghai"


class WorkspaceOut(BaseModel):
    """平台权限码 + 空间内成员角色 my_role（admin/developer/viewer）共同决定资源访问。"""

    id: int
    name: str
    description: Optional[str]
    owner_id: int
    timezone: str = "Asia/Shanghai"
    default_datasource_id: Optional[int] = None
    warehouse_datasource_id: Optional[int] = None
    effective_warehouse_datasource_id: Optional[int] = None
    my_role: Optional[str] = None  # 当前登录用户在该空间内的角色：admin/developer/viewer（负责人为 admin）

    class Config:
        from_attributes = True


class MemberAdd(BaseModel):
    user_id: int
    role: str = "developer"


class UserInviteCandidateOut(BaseModel):
    """空间管理员邀请成员时可选的用户账号（不含权限管理敏感字段）。"""
    id: int
    username: str
    email: str

    class Config:
        from_attributes = True


class WorkspaceUpdate(BaseModel):
    """PATCH 语义：仅提交需要修改的字段。负责人 owner_id 仅平台管理员可改。"""

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = None
    description: Optional[str] = None
    timezone: Optional[str] = None
    owner_id: Optional[int] = None


def _sort_workspaces(rows: list) -> list:
    """列表接口：默认空间 infras 排前，其余按 id 稳定排序，避免前端默认选错空间。"""

    def sort_key(w):
        is_def = 1 if getattr(w, "name", None) == "infras" else 0
        return (-is_def, w.id)

    return sorted(rows, key=sort_key)


def _workspace_out(db: Session, user: User, ws: Workspace) -> WorkspaceOut:
    wh = ws.warehouse_datasource_id or ws.default_datasource_id
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        description=ws.description,
        owner_id=ws.owner_id,
        timezone=ws.timezone or "Asia/Shanghai",
        default_datasource_id=ws.default_datasource_id,
        warehouse_datasource_id=ws.warehouse_datasource_id,
        effective_warehouse_datasource_id=wh,
        my_role=get_user_role(db, user, ws.id),
    )


@router.get("", response_model=List[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_can_list_workspaces(db, current_user)
    if is_platform_manager_role(db, current_user):
        rows = db.query(Workspace).all()
    else:
        member_ws_ids = [m.workspace_id for m in db.query(WorkspaceMember).filter(WorkspaceMember.user_id == current_user.id).all()]
        owned = db.query(Workspace).filter(Workspace.owner_id == current_user.id).all()
        member = db.query(Workspace).filter(Workspace.id.in_(member_ws_ids)).all()
        rows = list({w.id: w for w in owned + member}.values())
    rows = _sort_workspaces(rows)
    return [_workspace_out(db, current_user, w) for w in rows]


@router.post("", response_model=WorkspaceOut, dependencies=[Depends(require_platform_manager)])
def create_workspace(ws_in: WorkspaceCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """仅平台管理员可新建工作空间；创建者为 owner，并写入成员表为 admin。"""
    if db.query(Workspace).filter(Workspace.name == ws_in.name).first():
        raise HTTPException(status_code=400, detail="工作空间名称已存在")
    ws = Workspace(
        name=ws_in.name,
        description=ws_in.description,
        owner_id=current_user.id,
        timezone=ws_in.timezone,
    )
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=current_user.id, role="admin"))
    db.commit()
    db.refresh(ws)
    return _workspace_out(db, current_user, ws)


@router.get("/{ws_id}", response_model=WorkspaceOut)
def get_workspace(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    assert_workspace_access(db, current_user, ws_id)
    return _workspace_out(db, current_user, ws)


@router.put("/{ws_id}", response_model=WorkspaceOut)
def update_workspace(ws_id: int, ws_in: WorkspaceUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")

    payload = ws_in.model_dump(exclude_unset=True)
    meta_touch = {"name", "description", "timezone"} & payload.keys()

    if "owner_id" in payload:
        if not is_platform_manager_role(db, current_user):
            raise HTTPException(status_code=403, detail="仅平台管理员可变更工作空间负责人")
        owner_new = payload["owner_id"]
        if owner_new is None:
            raise HTTPException(status_code=400, detail="不支持将工作空间负责人置空，请指定有效用户 id")
        if owner_new != ws.owner_id:
            new_owner = db.query(User).filter(User.id == owner_new).first()
            if not new_owner:
                raise HTTPException(status_code=404, detail="新任负责人用户不存在")
            ws.owner_id = owner_new
            ex = db.query(WorkspaceMember).filter(
                WorkspaceMember.workspace_id == ws_id,
                WorkspaceMember.user_id == owner_new,
            ).first()
            if ex:
                ex.role = "admin"
            else:
                db.add(WorkspaceMember(workspace_id=ws_id, user_id=owner_new, role="admin"))

    if meta_touch:
        assert_can_edit_workspace_metadata(db, current_user, ws_id)
        for key in sorted(meta_touch):
            setattr(ws, key, payload[key])

    db.commit()
    db.refresh(ws)
    return _workspace_out(db, current_user, ws)


@router.post("/{ws_id}/members")
def add_member(ws_id: int, member_in: MemberAdd, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    assert_can_manage_workspace_members(db, current_user, ws_id)
    if member_in.role not in VALID_SPACE_MEMBER_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"无效的空间成员角色 role，可选: {', '.join(sorted(VALID_SPACE_MEMBER_ROLES))}",
        )
    if not db.query(User).filter(User.id == member_in.user_id).first():
        raise HTTPException(status_code=404, detail="用户不存在")
    existing = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == ws_id,
        WorkspaceMember.user_id == member_in.user_id
    ).first()
    if existing:
        existing.role = member_in.role
    else:
        db.add(WorkspaceMember(workspace_id=ws_id, user_id=member_in.user_id, role=member_in.role))
    db.commit()
    return {"message": "成员添加成功"}


@router.get("/{ws_id}/invite-user-candidates", response_model=List[UserInviteCandidateOut])
def list_invite_user_candidates(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """供空间管理员在本空间「添加成员」下拉使用；无需 platform system:user:read。"""
    if not db.query(Workspace).filter(Workspace.id == ws_id).first():
        raise HTTPException(status_code=404, detail="工作空间不存在")
    assert_can_manage_workspace_members(db, current_user, ws_id)
    rows = (
        db.query(User)
        .filter(User.is_active.is_(True))
        .order_by(User.username.asc())
        .all()
    )
    return [UserInviteCandidateOut(id=u.id, username=u.username, email=u.email) for u in rows]


@router.get("/{ws_id}/members")
def list_members(ws_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    assert_workspace_access(db, current_user, ws_id)
    members = db.query(WorkspaceMember).filter(WorkspaceMember.workspace_id == ws_id).all()
    result = []
    for m in members:
        u = db.query(User).filter(User.id == m.user_id).first()
        result.append(
            {
                "user_id": m.user_id,
                "username": u.username if u else "",
                "role": m.role,
                "is_owner": ws.owner_id == m.user_id,
            }
        )
    result.sort(key=lambda r: (0 if r["is_owner"] else 1, r["username"] or "", r["user_id"]))
    return result


@router.delete("/{ws_id}/members/{member_user_id}")
def remove_workspace_member(ws_id: int, member_user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    assert_can_manage_workspace_members(db, current_user, ws_id)
    if member_user_id == ws.owner_id:
        raise HTTPException(
            status_code=400,
            detail="不能直接移除现任负责人；请先在「变更负责人」中转移 owner_id 后再调整成员",
        )
    row = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == ws_id,
        WorkspaceMember.user_id == member_user_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="该成员不在此工作空间中")
    db.delete(row)
    db.commit()
    return {"message": "已移除成员"}

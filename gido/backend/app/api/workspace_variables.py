# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作空间全局变量 CRUD（Batch / Stream / Serve 引用 ${var_key}）。"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import User, WorkspaceVariable
from app.services.rbac import assert_can_edit_workspace_metadata, assert_workspace_access
from app.services.workspace_variables import VALID_SCOPES, mask_secret_value

router = APIRouter(prefix="/workspaces", tags=["工作空间变量"])


class WorkspaceVariableOut(BaseModel):
    id: int
    workspace_id: int
    var_key: str
    var_value: Optional[str] = None
    value_masked: Optional[str] = None
    is_secret: bool = False
    scope: str = "all"
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WorkspaceVariableCreate(BaseModel):
    var_key: str = Field(..., min_length=1, max_length=128)
    var_value: Optional[str] = None
    is_secret: bool = False
    scope: str = "all"
    description: Optional[str] = None


class WorkspaceVariableUpdate(BaseModel):
    var_key: Optional[str] = Field(None, min_length=1, max_length=128)
    var_value: Optional[str] = None
    is_secret: Optional[bool] = None
    scope: Optional[str] = None
    description: Optional[str] = None
    clear_value: bool = False


def _normalize_scope(scope: str) -> str:
    s = (scope or "all").strip().lower()
    if s not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail=f"scope 须为 {', '.join(sorted(VALID_SCOPES))}")
    return s


def _to_out(row: WorkspaceVariable, *, reveal_secrets: bool = False) -> WorkspaceVariableOut:
    val = row.var_value
    masked = mask_secret_value(val) if row.is_secret and val else None
    return WorkspaceVariableOut(
        id=row.id,
        workspace_id=row.workspace_id,
        var_key=row.var_key,
        var_value=val if (reveal_secrets or not row.is_secret) else masked,
        value_masked=masked if row.is_secret else None,
        is_secret=bool(row.is_secret),
        scope=row.scope or "all",
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{workspace_id}/variables", response_model=List[WorkspaceVariableOut])
def list_workspace_variables(
    workspace_id: int,
    scope: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_access(db, current_user, workspace_id)
    q = db.query(WorkspaceVariable).filter(WorkspaceVariable.workspace_id == workspace_id)
    if scope:
        s = _normalize_scope(scope)
        q = q.filter(WorkspaceVariable.scope.in_(["all", s]))
    rows = q.order_by(WorkspaceVariable.var_key.asc()).all()
    return [_to_out(r) for r in rows]


@router.post("/{workspace_id}/variables", response_model=WorkspaceVariableOut)
def create_workspace_variable(
    workspace_id: int,
    body: WorkspaceVariableCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_can_edit_workspace_metadata(db, current_user, workspace_id)
    key = body.var_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="var_key 不能为空")
    scope = _normalize_scope(body.scope)
    dup = (
        db.query(WorkspaceVariable)
        .filter(WorkspaceVariable.workspace_id == workspace_id, WorkspaceVariable.var_key == key)
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail=f"变量 {key} 已存在")
    row = WorkspaceVariable(
        workspace_id=workspace_id,
        var_key=key,
        var_value=body.var_value,
        is_secret=bool(body.is_secret),
        scope=scope,
        description=body.description,
        created_by=current_user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row, reveal_secrets=True)


@router.put("/{workspace_id}/variables/{var_id}", response_model=WorkspaceVariableOut)
def update_workspace_variable(
    workspace_id: int,
    var_id: int,
    body: WorkspaceVariableUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_can_edit_workspace_metadata(db, current_user, workspace_id)
    row = (
        db.query(WorkspaceVariable)
        .filter(WorkspaceVariable.id == var_id, WorkspaceVariable.workspace_id == workspace_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="变量不存在")
    patch = body.model_dump(exclude_unset=True)
    if "scope" in patch and patch["scope"] is not None:
        patch["scope"] = _normalize_scope(patch["scope"])
    if "var_key" in patch and patch["var_key"] is not None:
        new_key = patch["var_key"].strip()
        if new_key != row.var_key:
            dup = (
                db.query(WorkspaceVariable)
                .filter(
                    WorkspaceVariable.workspace_id == workspace_id,
                    WorkspaceVariable.var_key == new_key,
                    WorkspaceVariable.id != var_id,
                )
                .first()
            )
            if dup:
                raise HTTPException(status_code=409, detail=f"变量 {new_key} 已存在")
            patch["var_key"] = new_key
    if body.clear_value:
        row.var_value = None
    elif "var_value" in patch:
        row.var_value = patch.pop("var_value")
    for k, v in patch.items():
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _to_out(row, reveal_secrets=True)


@router.delete("/{workspace_id}/variables/{var_id}")
def delete_workspace_variable(
    workspace_id: int,
    var_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_can_edit_workspace_metadata(db, current_user, workspace_id)
    row = (
        db.query(WorkspaceVariable)
        .filter(WorkspaceVariable.id == var_id, WorkspaceVariable.workspace_id == workspace_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="变量不存在")
    db.delete(row)
    db.commit()
    return {"message": "已删除"}

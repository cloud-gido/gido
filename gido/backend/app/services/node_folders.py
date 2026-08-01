# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Studio / Stream 目录树：重挂父目录（整目录挪动）。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session


def folder_scope(folder: Any) -> str:
    return (getattr(folder, "scope", None) or "batch").strip().lower() or "batch"


def assert_valid_reparent(
    db: Session,
    folder: Any,
    new_parent_id: Optional[int],
    *,
    expected_scope: str,
) -> Any:
    """校验将 folder 挂到 new_parent_id（None=根）。返回父文件夹或 None。

    禁止：跨空间、跨 scope、挂到自身或子孙下（成环）。
    """
    want = (expected_scope or "batch").strip().lower()
    if folder_scope(folder) != want:
        raise HTTPException(status_code=404, detail="文件夹不存在")

    if new_parent_id is None:
        return None

    if int(new_parent_id) == int(folder.id):
        raise HTTPException(status_code=400, detail="不能将目录移动到自身下")

    Model = type(folder)
    parent = db.query(Model).filter_by(id=int(new_parent_id)).first()
    if not parent:
        raise HTTPException(status_code=404, detail="目标父目录不存在")
    if parent.workspace_id != folder.workspace_id:
        raise HTTPException(status_code=400, detail="目标父目录与工作空间不一致")
    if folder_scope(parent) != want:
        raise HTTPException(status_code=400, detail="目标父目录不属于同一目录树")

    # 沿父链上行：若碰到被移动目录则成环
    walk: Any = parent
    seen: set = set()
    while walk is not None:
        wid = int(walk.id)
        if wid in seen:
            break
        seen.add(wid)
        if wid == int(folder.id):
            raise HTTPException(status_code=400, detail="不能将目录移动到其子目录下")
        if walk.parent_id is None:
            break
        walk = db.query(Model).filter_by(id=int(walk.parent_id)).first()

    return parent


def reparent_folder(
    db: Session,
    folder: Any,
    new_parent_id: Optional[int],
    *,
    expected_scope: str,
) -> Any:
    """更新 parent_id，并按目标同级规则分配 sort_order（调用方 commit）。"""
    from app.services.tree_sort import sort_order_for_new_folder_peer

    same_parent = folder.parent_id == new_parent_id or (
        folder.parent_id is None and new_parent_id is None
    )
    if same_parent:
        return folder
    assert_valid_reparent(db, folder, new_parent_id, expected_scope=expected_scope)
    folder.parent_id = new_parent_id
    model = type(folder)
    if hasattr(model, "workspace_id") and hasattr(model, "parent_id") and hasattr(model, "scope"):
        folder.sort_order = sort_order_for_new_folder_peer(
            db,
            workspace_id=int(folder.workspace_id),
            parent_id=new_parent_id,
            scope=folder_scope(folder),
            folder_model=model,
        )
    else:
        # 单测用 SimpleNamespace 等非 ORM 对象
        folder.sort_order = 0
    db.add(folder)
    db.flush()
    return folder

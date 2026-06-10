# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""工作空间数据源继承策略（与前端 workspaceDatasource.ts 一致）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.workspace import DataSource, Workspace


def resolve_datasource_id(
    db: Session,
    *,
    workspace_id: int,
    explicit_datasource_id: Optional[int],
) -> Optional[int]:
    """节点/脚本已配置则用配置值，否则用工作空间默认（与 effective_warehouse 一致：默认优先，否则数仓）。"""
    if explicit_datasource_id:
        return int(explicit_datasource_id)
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        return None
    if ws.default_datasource_id:
        return int(ws.default_datasource_id)
    if ws.warehouse_datasource_id:
        return int(ws.warehouse_datasource_id)
    return None


def load_datasource_for_run(
    db: Session,
    *,
    workspace_id: int,
    explicit_datasource_id: Optional[int],
    role: str = "数据源",
) -> DataSource:
    ds_id = resolve_datasource_id(db, workspace_id=workspace_id, explicit_datasource_id=explicit_datasource_id)
    if not ds_id:
        raise ValueError(
            f"{role}未单独配置，且工作空间未设置默认数据源；请在脚本/节点「配置」或「空间设置」中指定"
        )
    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds:
        raise ValueError(f"{role}不存在（id={ds_id}）")
    if ds.workspace_id != workspace_id:
        raise ValueError(f"{role}不属于当前工作空间")
    if not ds.is_active:
        raise ValueError(f"数据源「{ds.name}」已停用")
    return ds

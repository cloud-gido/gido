# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""工作空间级设置：默认数据源、数仓数据源、平台集成行。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.workspace import DataSource, Workspace, WorkspacePlatformIntegration


def _validate_ds_in_workspace(db: Session, workspace_id: int, ds_id: Optional[int], label: str) -> None:
    if ds_id is None:
        return
    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds or ds.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail=f"{label}须属于当前工作空间")
    if not ds.is_active:
        raise HTTPException(status_code=400, detail=f"{label}对应的数据源已停用")


def ensure_workspace_platform_row(db: Session, workspace_id: int) -> WorkspacePlatformIntegration:
    row = (
        db.query(WorkspacePlatformIntegration)
        .filter(WorkspacePlatformIntegration.workspace_id == workspace_id)
        .first()
    )
    if row:
        return row
    row = WorkspacePlatformIntegration(workspace_id=workspace_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_workspace_defaults(db: Session, workspace_id: int) -> Dict[str, Any]:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    wh = ws.warehouse_datasource_id or ws.default_datasource_id
    return {
        "default_datasource_id": ws.default_datasource_id,
        "warehouse_datasource_id": ws.warehouse_datasource_id,
        "effective_warehouse_datasource_id": wh,
        "default_node_timeout_seconds": getattr(ws, "default_node_timeout_seconds", None) or 3600,
        "default_node_retry_times": getattr(ws, "default_node_retry_times", None)
        if getattr(ws, "default_node_retry_times", None) is not None
        else 3,
        "default_node_retry_interval_minutes": getattr(ws, "default_node_retry_interval_minutes", None)
        if getattr(ws, "default_node_retry_interval_minutes", None) is not None
        else 1,
    }


def update_workspace_defaults(
    db: Session,
    workspace_id: int,
    *,
    default_datasource_id: Optional[int] = None,
    warehouse_datasource_id: Optional[int] = None,
    clear_default: bool = False,
    clear_warehouse: bool = False,
    default_node_timeout_seconds: Optional[int] = None,
    default_node_retry_times: Optional[int] = None,
    default_node_retry_interval_minutes: Optional[int] = None,
) -> Workspace:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    if clear_default:
        ws.default_datasource_id = None
    elif default_datasource_id is not None:
        _validate_ds_in_workspace(db, workspace_id, default_datasource_id, "默认数据源")
        ws.default_datasource_id = default_datasource_id
    if clear_warehouse:
        ws.warehouse_datasource_id = None
    elif warehouse_datasource_id is not None:
        _validate_ds_in_workspace(db, workspace_id, warehouse_datasource_id, "数仓数据源")
        ws.warehouse_datasource_id = warehouse_datasource_id
    if default_node_timeout_seconds is not None:
        ws.default_node_timeout_seconds = max(60, min(86400 * 7, int(default_node_timeout_seconds)))
    if default_node_retry_times is not None:
        ws.default_node_retry_times = max(0, min(20, int(default_node_retry_times)))
    if default_node_retry_interval_minutes is not None:
        ws.default_node_retry_interval_minutes = max(0, min(1440, int(default_node_retry_interval_minutes)))
    db.commit()
    db.refresh(ws)
    return ws


def resolve_effective_datasource_ids(db: Session, workspace_id: int) -> Tuple[Optional[int], Optional[int]]:
    """返回 (default_ds_id, warehouse_ds_id)，warehouse 未配置时回退 default。"""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        return None, None
    default_id = ws.default_datasource_id
    warehouse_id = ws.warehouse_datasource_id or default_id
    return default_id, warehouse_id

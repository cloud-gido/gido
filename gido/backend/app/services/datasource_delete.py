# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据源删除：业界常见「占用则拒绝硬删」，不级联抹掉业务对象。"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.data_service import DataApi
from app.models.workspace import (
    AdhocRun,
    DataSource,
    MetaTable,
    SyncTask,
    TaskNode,
    Workspace,
)


def collect_datasource_usages(db: Session, ds_id: int) -> List[Dict[str, Any]]:
    """返回占用摘要；blocking=False 的项可在删除前自动断开。"""
    usages: List[Dict[str, Any]] = []

    node_q = db.query(TaskNode).filter(TaskNode.datasource_id == ds_id)
    node_n = node_q.count()
    if node_n:
        usages.append(
            {
                "type": "task_node",
                "label": "数据开发节点",
                "count": node_n,
                "samples": [r.name for r in node_q.limit(5).all()],
                "blocking": True,
            }
        )

    api_q = db.query(DataApi).filter(DataApi.datasource_id == ds_id)
    api_n = api_q.count()
    if api_n:
        usages.append(
            {
                "type": "data_api",
                "label": "数据服务 API",
                "count": api_n,
                "samples": [r.api_code or r.name for r in api_q.limit(5).all()],
                "blocking": True,
            }
        )

    sync_q = db.query(SyncTask).filter(
        (SyncTask.src_datasource_id == ds_id) | (SyncTask.dst_datasource_id == ds_id)
    )
    sync_n = sync_q.count()
    if sync_n:
        usages.append(
            {
                "type": "sync_task",
                "label": "数据集成任务",
                "count": sync_n,
                "samples": [r.name for r in sync_q.limit(5).all()],
                "blocking": True,
            }
        )

    meta_q = db.query(MetaTable).filter(MetaTable.datasource_id == ds_id)
    meta_n = meta_q.count()
    if meta_n:
        usages.append(
            {
                "type": "meta_table",
                "label": "数据字典表",
                "count": meta_n,
                "samples": [r.table_name for r in meta_q.limit(5).all()],
                "blocking": True,
            }
        )

    ws_default = (
        db.query(Workspace)
        .filter(
            (Workspace.default_datasource_id == ds_id)
            | (Workspace.warehouse_datasource_id == ds_id)
        )
        .all()
    )
    if ws_default:
        usages.append(
            {
                "type": "workspace_default",
                "label": "工作空间默认/仓数据源",
                "count": len(ws_default),
                "samples": [w.name for w in ws_default[:5]],
                "blocking": True,
            }
        )

    adhoc_n = db.query(AdhocRun).filter(AdhocRun.datasource_id == ds_id).count()
    if adhoc_n:
        usages.append(
            {
                "type": "adhoc_run",
                "label": "探查/临时运行记录",
                "count": adhoc_n,
                "samples": [],
                "blocking": False,
            }
        )

    return usages


def assert_datasource_deletable(db: Session, ds: DataSource) -> None:
    usages = collect_datasource_usages(db, int(ds.id))
    blocking = [u for u in usages if u.get("blocking", True)]
    if blocking:
        parts = []
        for u in blocking:
            sample = "、".join(str(x) for x in (u.get("samples") or [])[:3])
            suffix = f"（如 {sample}）" if sample else ""
            parts.append(f"{u['label']} {u['count']} 个{suffix}")
        raise HTTPException(
            status_code=409,
            detail="数据源仍被占用，无法删除。请先解除引用，或在编辑中将其停用："
            + "；".join(parts),
        )
    # 非阻塞历史引用：断开后允许删
    db.query(AdhocRun).filter(AdhocRun.datasource_id == ds.id).update(
        {AdhocRun.datasource_id: None},
        synchronize_session=False,
    )

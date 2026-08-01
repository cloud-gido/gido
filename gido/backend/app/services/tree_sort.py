# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""目录树叶子排序：默认字典序；用户拖拽后以 sort_order 为准。"""
from __future__ import annotations

from typing import Any, List, Optional, Type

from sqlalchemy.orm import Session, Query


def _peers_q(db: Session, model: Type[Any], workspace_id: int, folder_id: Optional[int]) -> Query:
    q = db.query(model).filter(model.workspace_id == int(workspace_id))
    if folder_id is None:
        q = q.filter(model.folder_id.is_(None))
    else:
        q = q.filter(model.folder_id == int(folder_id))
    return q


def _peer_sort_orders(
    db: Session,
    model: Type[Any],
    workspace_id: int,
    folder_id: Optional[int],
) -> List[int]:
    rows = _peers_q(db, model, workspace_id, folder_id).all()
    return [int(getattr(r, "sort_order", 0) or 0) for r in rows]


def folder_has_manual_leaf_order(
    db: Session,
    model: Type[Any],
    workspace_id: int,
    folder_id: Optional[int],
) -> bool:
    """同目录内是否已有用户拖拽产生的 sort_order>0。"""
    return any(o > 0 for o in _peer_sort_orders(db, model, workspace_id, folder_id))


def sort_order_for_new_peer(
    db: Session,
    model: Type[Any],
    workspace_id: int,
    folder_id: Optional[int],
) -> int:
    """新建或移入同目录时的 sort_order。

    - 目录仍为自然序（全员 sort_order=0）→ 返回 0，展示按名称字典序
    - 目录已手动排过序 → 追加到末尾 max+10
    """
    orders = _peer_sort_orders(db, model, workspace_id, folder_id)
    if not any(o > 0 for o in orders):
        return 0
    return max(orders) + 10

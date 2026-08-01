# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""树排序：默认字典序 / 手工 sort_order。"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.tree_sort import folder_has_manual_leaf_order, sort_order_for_new_peer


class _Model:
    workspace_id = object()
    folder_id = SimpleNamespace(is_=lambda *a, **k: "IS_NULL")
    sort_order = object()


def _db(peers, folder_id=None):
    class Q:
        def __init__(self, rows):
            self.rows = list(rows)

        def filter(self, *a, **k):
            return self

        def all(self):
            return list(self.rows)

    class DB:
        def query(self, model):
            return Q(peers)

    return DB()


def test_natural_folder_new_peer_zero():
    peers = [SimpleNamespace(sort_order=0), SimpleNamespace(sort_order=0)]
    db = _db(peers)
    assert folder_has_manual_leaf_order(db, _Model, 1, None) is False
    assert sort_order_for_new_peer(db, _Model, 1, None) == 0


def test_manual_folder_appends_after_max():
    peers = [
        SimpleNamespace(sort_order=10),
        SimpleNamespace(sort_order=30),
        SimpleNamespace(sort_order=0),
    ]
    db = _db(peers)
    assert folder_has_manual_leaf_order(db, _Model, 1, None) is True
    assert sort_order_for_new_peer(db, _Model, 1, None) == 40


def test_folder_peer_sort_order():
    from app.services.tree_sort import sort_order_for_new_folder_peer

    class Folder:
        workspace_id = object()
        parent_id = SimpleNamespace(is_=lambda *a, **k: "IS_NULL")
        scope = object()
        sort_order = object()

    class Q:
        def __init__(self, rows):
            self.rows = list(rows)

        def filter(self, *a, **k):
            return self

        def all(self):
            return list(self.rows)

    class DB:
        def query(self, model):
            return Q([SimpleNamespace(sort_order=0), SimpleNamespace(sort_order=0)])

    assert sort_order_for_new_folder_peer(
        DB(), workspace_id=1, parent_id=None, scope="batch", folder_model=Folder
    ) == 0

    class DB2:
        def query(self, model):
            return Q([SimpleNamespace(sort_order=10), SimpleNamespace(sort_order=40)])

    assert sort_order_for_new_folder_peer(
        DB2(), workspace_id=1, parent_id=None, scope="batch", folder_model=Folder
    ) == 50

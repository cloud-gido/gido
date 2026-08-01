# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""目录整棵挪动：成环检测与重挂父目录。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.node_folders import assert_valid_reparent, reparent_folder


def _folder(id: int, workspace_id: int = 1, parent_id=None, scope="batch", sort_order=0):
    return SimpleNamespace(
        id=id, workspace_id=workspace_id, parent_id=parent_id, scope=scope, sort_order=sort_order
    )


def _db_returning(sequence):
    """每次 .query().filter_by(...).first() 依次返回 sequence 中的元素。"""
    items = list(sequence)
    db = MagicMock()

    def query(_model):
        q = MagicMock()

        def filter_by(**_k):
            fq = MagicMock()
            fq.first = lambda: items.pop(0) if items else None
            return fq

        def filter(*_a, **_k):
            fq = MagicMock()
            fq.first = lambda: items.pop(0) if items else None
            fq.all = lambda: []
            return fq

        q.filter_by = filter_by
        q.filter = filter
        q.all = lambda: []
        return q

    db.query.side_effect = query
    return db


def test_reparent_to_root_ok():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = []
    db.query.return_value = q
    folder = _folder(2, parent_id=1)
    out = reparent_folder(db, folder, None, expected_scope="batch")
    assert out.parent_id is None
    assert out.sort_order == 0
    db.add.assert_called()


def test_reparent_noop_same_parent():
    db = MagicMock()
    folder = _folder(2, parent_id=None)
    out = reparent_folder(db, folder, None, expected_scope="batch")
    assert out is folder
    db.add.assert_not_called()


def test_reparent_self_rejected():
    db = MagicMock()
    folder = _folder(2, parent_id=None)
    with pytest.raises(HTTPException) as ei:
        assert_valid_reparent(db, folder, 2, expected_scope="batch")
    assert ei.value.status_code == 400
    assert "自身" in ei.value.detail


def test_reparent_into_descendant_rejected():
    """A → B → C；不能把 A 挂到 C 下。"""
    a = _folder(1, parent_id=None)
    b = _folder(2, parent_id=1)
    c = _folder(3, parent_id=2)
    db = _db_returning([c, b, a])
    with pytest.raises(HTTPException) as ei:
        assert_valid_reparent(db, a, 3, expected_scope="batch")
    assert ei.value.status_code == 400
    assert "子目录" in ei.value.detail


def test_reparent_under_valid_parent_ok():
    a = _folder(1, parent_id=None)
    b = _folder(2, parent_id=None)
    # parent lookup → b；b.parent_id is None → stop
    db = _db_returning([b])
    parent = assert_valid_reparent(db, a, 2, expected_scope="batch")
    assert parent is b


def test_wrong_scope_rejected():
    db = MagicMock()
    folder = _folder(1, scope="stream")
    with pytest.raises(HTTPException) as ei:
        assert_valid_reparent(db, folder, None, expected_scope="batch")
    assert ei.value.status_code == 404

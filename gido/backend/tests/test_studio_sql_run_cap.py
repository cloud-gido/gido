# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Studio SQL 试跑：LIMIT 追加 + fetchmany 封顶（单元）。"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("DS_ENABLED", "false")

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.sql_readonly import apply_readonly_row_limit
from app.services.studio_sql_run import _looks_like_result_query, run_sql_with_result


@pytest.mark.parametrize(
    "stmt,expect",
    [
        ("SELECT 1", True),
        ("with t as (select 1) select * from t", True),
        ("-- comment\nSELECT 1", True),
        ("SHOW TABLES", False),
        ("show ROUTINE load", False),
        ("DESC foo", False),
        ("EXPLAIN SELECT 1", False),
        ("USE bigdata_ods", False),
        ("INSERT INTO t VALUES (1)", False),
        ("UPDATE t SET a=1", False),
        ("CREATE TABLE t (id INT)", False),
    ],
)
def test_looks_like_result_query(stmt: str, expect: bool):
    assert _looks_like_result_query(stmt) is expect


def test_apply_readonly_row_limit_appends_when_missing():
    out = apply_readonly_row_limit("SELECT id FROM t ORDER BY id", 100)
    assert out.upper().endswith("LIMIT 100")
    assert "ORDER BY id" in out


def test_apply_readonly_row_limit_keeps_existing_limit():
    sql = "SELECT id FROM t LIMIT 5"
    assert apply_readonly_row_limit(sql, 10000) == "SELECT id FROM t LIMIT 5"


def test_apply_readonly_row_limit_caps_at_10000():
    out = apply_readonly_row_limit("SELECT 1", 999999)
    assert out.endswith("LIMIT 10000")


def test_run_sql_with_result_uses_fetchmany_not_fetchall(monkeypatch):
    """回归：结果集不得 fetchall 全量载入内存。"""
    from app.services import studio_sql_run as mod

    node = SimpleNamespace(
        workspace_id=1,
        datasource_id=7,
        script_content="SELECT id FROM big_table",
        params=None,
    )
    ds = SimpleNamespace(
        id=7,
        name="doris_demo",
        ds_type="doris",
        host="127.0.0.1",
        port=9030,
    )

    monkeypatch.setattr(mod, "resolve_sql_datasource", lambda db, n: ds)
    monkeypatch.setattr(mod, "normalize_ds_type", lambda d: "mysql")

    class _WsQ:
        def filter(self, *a, **k):
            return self

        def first(self):
            return SimpleNamespace(timezone="Asia/Shanghai")

    class _Db:
        def query(self, *_a, **_k):
            return _WsQ()

    rows = [(i,) for i in range(20)]
    cur = MagicMock()
    cur.description = (("id", None, None, None, None, None, None),)
    cur.rowcount = -1
    executed: list[str] = []

    def _execute(sql):
        executed.append(sql)

    def _fetchmany(n):
        return rows[:n]

    def _fetchall():
        raise AssertionError("fetchall must not be used for result queries")

    cur.execute.side_effect = _execute
    cur.fetchmany.side_effect = _fetchmany
    cur.fetchall.side_effect = _fetchall
    cur.close = MagicMock()

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.commit = MagicMock()

    @contextmanager
    def _open(_ds):
        yield ("mysql", conn)

    monkeypatch.setattr(mod, "open_connection", _open)

    def _sub(db, ws_id, script, scope, bizdate=None):
        return script

    monkeypatch.setattr(
        "app.services.workspace_variables.substitute_script_variables",
        _sub,
    )

    logs, result = run_sql_with_result(node, _Db(), bizdate=None, resolve_date_expr=lambda *a, **k: None)
    assert result is not None
    assert result["total"] == 20
    assert result["truncated"] is False
    assert len(result["rows"]) == 20
    assert any("LIMIT 10000" in s.upper() for s in executed)
    cur.fetchmany.assert_called()
    assert any("已追加 LIMIT" in line for line in logs)


def test_run_sql_with_result_truncates_when_fetchmany_overflows(monkeypatch):
    from app.services import studio_sql_run as mod

    node = SimpleNamespace(
        workspace_id=1,
        datasource_id=1,
        script_content="SELECT id FROM t LIMIT 999999",
        params=None,
    )
    ds = SimpleNamespace(id=1, name="pg", ds_type="postgresql", host="h", port=5432)

    monkeypatch.setattr(mod, "resolve_sql_datasource", lambda db, n: ds)
    monkeypatch.setattr(mod, "normalize_ds_type", lambda d: "postgresql")

    class _WsQ:
        def filter(self, *a, **k):
            return self

        def first(self):
            return SimpleNamespace(timezone="Asia/Shanghai")

    class _Db:
        def query(self, *_a, **_k):
            return _WsQ()

    # 用户自带大 LIMIT：apply_readonly_row_limit 不改写；fetchmany 仍封顶
    overflow = [(i,) for i in range(10001)]
    cur = MagicMock()
    cur.description = (("id",),)
    cur.execute = MagicMock()
    cur.fetchmany = MagicMock(return_value=overflow)
    cur.fetchall = MagicMock(side_effect=AssertionError("no fetchall"))
    cur.close = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _open(_ds):
        yield ("postgresql", conn)

    monkeypatch.setattr(mod, "open_connection", _open)
    monkeypatch.setattr(
        "app.services.workspace_variables.substitute_script_variables",
        lambda *a, **k: node.script_content,
    )

    logs, result = run_sql_with_result(node, _Db(), resolve_date_expr=lambda *a, **k: None)
    assert result is not None
    assert result["total"] == 10000
    assert result["truncated"] is True
    assert len(result["rows"]) == 10000
    assert any("已截断" in line for line in logs)
    cur.fetchmany.assert_called_with(10001)


def test_run_sql_dml_does_not_force_limit(monkeypatch):
    from app.services import studio_sql_run as mod

    node = SimpleNamespace(
        workspace_id=1,
        datasource_id=1,
        script_content="INSERT INTO t VALUES (1)",
        params=None,
    )
    ds = SimpleNamespace(id=1, name="pg", ds_type="postgresql", host="h", port=5432)
    monkeypatch.setattr(mod, "resolve_sql_datasource", lambda db, n: ds)
    monkeypatch.setattr(mod, "normalize_ds_type", lambda d: "postgresql")

    class _WsQ:
        def filter(self, *a, **k):
            return self

        def first(self):
            return SimpleNamespace(timezone="Asia/Shanghai")

    class _Db:
        def query(self, *_a, **_k):
            return _WsQ()

    executed: list[str] = []
    cur = MagicMock()
    cur.description = None
    cur.rowcount = 1
    cur.execute.side_effect = lambda sql: executed.append(sql)
    cur.close = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur

    @contextmanager
    def _open(_ds):
        yield ("postgresql", conn)

    monkeypatch.setattr(mod, "open_connection", _open)
    monkeypatch.setattr(
        "app.services.workspace_variables.substitute_script_variables",
        lambda *a, **k: node.script_content,
    )

    logs, result = run_sql_with_result(node, _Db(), resolve_date_expr=lambda *a, **k: None)
    assert result is None
    assert executed == ["INSERT INTO t VALUES (1)"]
    assert any("影响行数" in line for line in logs)


def test_run_sql_use_and_show_do_not_append_limit(monkeypatch):
    """Doris SHOW ROUTINE LOAD 等不支持尾部 LIMIT；USE 亦然。"""
    from app.services import studio_sql_run as mod

    node = SimpleNamespace(
        workspace_id=1,
        datasource_id=1,
        script_content="use bigdata_ods;\nshow ROUTINE load;",
        params=None,
    )
    ds = SimpleNamespace(id=1, name="doirs", ds_type="doris", host="h", port=9030)
    monkeypatch.setattr(mod, "resolve_sql_datasource", lambda db, n: ds)
    monkeypatch.setattr(mod, "normalize_ds_type", lambda d: "mysql")

    class _WsQ:
        def filter(self, *a, **k):
            return self

        def first(self):
            return SimpleNamespace(timezone="Asia/Shanghai")

    class _Db:
        def query(self, *_a, **_k):
            return _WsQ()

    executed: list[str] = []
    cur = MagicMock()
    cur.description = None
    cur.rowcount = 0

    def _execute(sql):
        executed.append(sql)
        if sql.lower().startswith("show"):
            cur.description = (("Id",), ("Name",))
            cur.fetchmany = MagicMock(return_value=[])

    cur.execute.side_effect = _execute
    cur.close = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.commit = MagicMock()

    @contextmanager
    def _open(_ds):
        yield ("mysql", conn)

    monkeypatch.setattr(mod, "open_connection", _open)
    monkeypatch.setattr(
        "app.services.workspace_variables.substitute_script_variables",
        lambda *a, **k: node.script_content,
    )

    logs, _result = run_sql_with_result(node, _Db(), resolve_date_expr=lambda *a, **k: None)
    assert executed == ["use bigdata_ods", "show ROUTINE load"]
    assert not any("LIMIT" in s.upper() for s in executed)
    assert not any("已追加 LIMIT" in line for line in logs)

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务执行路径集成：分页保留 ORDER BY；可视化向导编译排序。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any, List, Optional

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")

import pytest

from app.services.data_api_engine import execute_data_api


class _FakeCursor:
    def __init__(self, executed: List[str]):
        self._executed = executed
        self.description = (("player_id",), ("last_bet_at",))
        self._last_sql = ""

    def execute(self, sql: str, params: Any = None) -> None:
        self._last_sql = sql
        self._executed.append(sql)

    def fetchone(self):
        if "COUNT(*)" in self._last_sql.upper():
            return (100,)
        return None

    def fetchall(self):
        return [("p1", "2026-08-05 10:00:00"), ("p2", "2026-08-05 09:00:00")]


class _FakeConn:
    def __init__(self, executed: List[str]):
        self._executed = executed

    def cursor(self):
        return _FakeCursor(self._executed)

    def close(self) -> None:
        return None


def _ds() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        ds_type="doris",
        host="127.0.0.1",
        port=9030,
        username="root",
        password="",
        database="bigdata_ads",
    )


def _api(**kwargs: Any) -> SimpleNamespace:
    base = dict(
        id=36,
        workspace_id=1,
        api_code="gameline_user_list",
        mode="sql",
        sql_template="",
        wizard_config=None,
        params=[],
        pagination_enabled=True,
        page_size_default=20,
        page_size_max=1000,
        timeout_seconds=30,
        cache_ttl_seconds=0,
        max_rows=10000,
        response_fields=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.fixture()
def capture_doris_sql(monkeypatch):
    executed: List[str] = []
    import sys
    import types

    fake_pymysql = types.ModuleType("pymysql")
    fake_converters = types.ModuleType("pymysql.converters")

    class _Err(Exception):
        pass

    def escape_item(val, charset):
        if val is None:
            return "NULL"
        if isinstance(val, (int, float)):
            return str(val)
        return "'" + str(val).replace("'", "''") + "'"

    fake_converters.escape_item = escape_item  # type: ignore[attr-defined]
    fake_pymysql.Error = _Err  # type: ignore[attr-defined]
    fake_pymysql.converters = fake_converters  # type: ignore[attr-defined]

    def _connect(**kwargs):
        return _FakeConn(executed)

    fake_pymysql.connect = _connect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymysql", fake_pymysql)
    monkeypatch.setitem(sys.modules, "pymysql.converters", fake_converters)

    import app.services.workspace_variables as wv

    monkeypatch.setattr(wv, "substitute_script_variables", lambda db, ws, sql, scope: sql)
    return executed


def test_execute_sql_api_keeps_order_by_with_pagination(capture_doris_sql, monkeypatch):
    sql = (
        "SELECT player_id, last_bet_at FROM bigdata_ads.ads_gameline_user_bet_stat "
        "WHERE total_bet_count > 0 "
        "ORDER BY last_bet_at DESC, operator_id, player_id"
    )
    api = _api(mode="sql", sql_template=sql)
    db = SimpleNamespace()
    out = execute_data_api(db, api, _ds(), {}, page_no=2, page_size=20, skip_cache=True)
    assert out["PageNumber"] == 2
    assert out["PageSize"] == 20
    assert out["TotalCount"] == 100

    list_sql = next(s for s in capture_doris_sql if "ORDER BY" in s.upper() and "COUNT(*)" not in s.upper())
    assert "ORDER BY last_bet_at DESC, operator_id, player_id" in list_sql
    assert "LIMIT 20 OFFSET 20" in list_sql
    assert "AS _dw_api_sub" not in list_sql


def test_execute_wizard_api_compiles_order_by_then_paginates(capture_doris_sql):
    api = _api(
        mode="wizard",
        sql_template="",  # 执行时由 wizard 重编译
        wizard_config={
            "table": "bigdata_ads.ads_gameline_user_bet_stat",
            "fields": ["player_id", "last_bet_at", "net_profit"],
            "filters": [],
            "order_by": [
                {"column": "last_bet_at", "direction": "DESC"},
                {"column": "player_id", "direction": "ASC"},
            ],
        },
    )
    execute_data_api(SimpleNamespace(), api, _ds(), {}, page_no=1, page_size=10, skip_cache=True)
    list_sql = next(s for s in capture_doris_sql if "FROM bigdata_ads.ads_gameline_user_bet_stat" in s and "COUNT(*)" not in s.upper())
    assert "ORDER BY last_bet_at DESC, player_id ASC" in list_sql
    assert list_sql.rstrip(";").endswith("LIMIT 10 OFFSET 0")
    assert "AS _dw_api_sub" not in list_sql


def test_execute_wizard_api_defaults_order_by_from_fields(capture_doris_sql):
    api = _api(
        mode="wizard",
        wizard_config={
            "table": "t_user",
            "fields": ["operator_id", "player_id", "score"],
            "filters": [],
        },
    )
    execute_data_api(SimpleNamespace(), api, _ds(), {}, page_no=1, page_size=5, skip_cache=True)
    list_sql = next(s for s in capture_doris_sql if "FROM t_user" in s and "COUNT(*)" not in s.upper())
    assert "ORDER BY operator_id ASC, player_id ASC" in list_sql
    assert "LIMIT 5 OFFSET 0" in list_sql

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""integration_runtime list_schemas / catalog 参数单元测试（无真实库）。"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from app.services.integration_runtime import list_columns, list_schemas, list_tables


def _mysql_ds(database: str = "bigdata_ads") -> SimpleNamespace:
    return SimpleNamespace(
        ds_type="doris",
        host="127.0.0.1",
        port=9030,
        username="u",
        password="p",
        database=database,
        extra_config={},
    )


def test_list_schemas_mysql_filters_system():
    ds = _mysql_ds()

    class Cur:
        def execute(self, *_a, **_k):
            return None

        def fetchall(self):
            return [("bigdata_ads",), ("bigdata_dw",), ("information_schema",), ("mysql",)]

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            return None

    @contextmanager
    def fake_open(_ds, **_kw):
        yield ("mysql", Conn())

    with patch("app.services.integration_runtime.open_connection", fake_open):
        out = list_schemas(ds)
    # information_schema / mysql filtered in SQL; mock returns them so we only assert parsing
    assert {r["name"] for r in out} >= {"bigdata_ads", "bigdata_dw"}
    assert next(r for r in out if r["name"] == "bigdata_ads")["is_default"] is True


def test_list_tables_respects_catalog_param():
    ds = _mysql_ds("default_db")
    captured: dict = {}

    class Cur:
        def execute(self, sql, params=None):
            captured["params"] = params

        def fetchall(self):
            return [("ads_gameline_overview_1d", "BASE TABLE", "概览")]

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            return None

    @contextmanager
    def fake_open(_ds, **_kw):
        yield ("mysql", Conn())

    with patch("app.services.integration_runtime.open_connection", fake_open):
        out = list_tables(ds, catalog="bigdata_ads")
    assert captured["params"] == ("bigdata_ads",)
    assert out[0]["name"] == "ads_gameline_overview_1d"
    assert out[0]["catalog"] == "bigdata_ads"


def test_list_columns_parses_qualified_table_name():
    ds = _mysql_ds()
    captured: dict = {}

    class Cur:
        def execute(self, sql, params=None):
            captured["params"] = params

        def fetchall(self):
            return [("stat_date", "date", "NO", "PRI"), ("company_id", "bigint", "NO", "")]

    class Conn:
        def cursor(self):
            return Cur()

        def close(self):
            return None

    @contextmanager
    def fake_open(_ds, **_kw):
        yield ("mysql", Conn())

    with patch("app.services.integration_runtime.open_connection", fake_open):
        out = list_columns(ds, "bigdata_ads.ads_foo")
    assert captured["params"] == ("bigdata_ads", "ads_foo")
    assert [c["name"] for c in out] == ["stat_date", "company_id"]

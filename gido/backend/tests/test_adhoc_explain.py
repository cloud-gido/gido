# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app.models.workspace import AdhocRunStatement, DataSource
from app.services import adhoc_explain


class _Cursor:
    description = (("QUERY PLAN", 25, None, None, None, None, True),)

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return [("Seq Scan on orders",)]

    def close(self):
        return None


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def get_backend_pid(self):
        return 42


class _Db:
    def commit(self):
        return None

    def refresh(self, _statement):
        return None


def test_capture_statement_plan_is_plain_explain(monkeypatch):
    cursor = _Cursor()

    @contextmanager
    def _open(_datasource):
        yield ("postgresql", _Connection(cursor), "public")

    monkeypatch.setattr(adhoc_explain, "open_connection", _open)
    datasource = DataSource(ds_type="postgresql")
    statement = AdhocRunStatement(sql_text="SELECT * FROM orders", status="success")
    snapshot = adhoc_explain.capture_statement_plan(
        _Db(), datasource=datasource, statement=statement
    )
    sql_commands = [item[0] for item in cursor.executed]
    assert "EXPLAIN SELECT * FROM orders" in sql_commands
    assert all("ANALYZE" not in sql.upper() for sql in sql_commands)
    assert snapshot["query_id"] == "42"
    assert snapshot["fields"][0]["name"] == "QUERY PLAN"
    assert statement.plan_snapshot == snapshot


def test_capture_statement_plan_rejects_non_dql(monkeypatch):
    monkeypatch.setattr(
        adhoc_explain,
        "open_connection",
        lambda _datasource: pytest.fail("must reject before connecting"),
    )
    with pytest.raises(HTTPException):
        adhoc_explain.capture_statement_plan(
            _Db(),
            datasource=DataSource(ds_type="postgresql"),
            statement=AdhocRunStatement(
                sql_text="DELETE FROM orders", status="success"
            ),
        )

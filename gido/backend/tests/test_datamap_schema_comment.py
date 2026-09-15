# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据地图结构同步须写入 COLUMN_COMMENT（与数据开发同源），且禁止全表 COUNT(*)。"""
from __future__ import annotations

import os
from contextlib import contextmanager

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-datamap-comment")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.workspace import DataSource, MetaColumn, MetaTable, User, Workspace
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.api import datamap as datamap_api
from app.api.datamap import _sync_table_schema


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(User(id=1, username="u", email="u@t.local", hashed_password="x", is_active=True))
    s.add(Workspace(id=1, name="ws", owner_id=1))
    s.add(
        DataSource(
            id=10,
            workspace_id=1,
            name="doris",
            ds_type="doris",
            host="h",
            port=9030,
            database="bigdata_dw",
            created_by=1,
        )
    )
    s.add(
        MetaTable(
            id=100,
            workspace_id=1,
            datasource_id=10,
            db_name="bigdata_dw",
            table_name="ads_orders",
        )
    )
    s.commit()
    yield s
    s.close()


def test_sync_table_schema_persists_comments_and_estimated_rows(db, monkeypatch):
    def _fake_list_columns(ds, table_name, catalog=None):
        assert table_name == "ads_orders"
        assert catalog == "bigdata_dw"
        return [
            {
                "name": "order_id",
                "type": "bigint",
                "nullable": False,
                "key": "PRI",
                "comment": "订单ID",
            },
            {
                "name": "amount",
                "type": "decimal(18,2)",
                "nullable": True,
                "key": "",
                "comment": "订单金额",
            },
        ]

    executed: list[str] = []

    @contextmanager
    def _fake_open_connection(ds, *, database=None):
        class Cur:
            def execute(self, sql, *_a, **_k):
                executed.append(str(sql))
                return None

            def fetchone(self):
                # TABLE_COMMENT, TABLE_ROWS
                return ("订单宽表", 128000)

        class Conn:
            def cursor(self):
                return Cur()

        yield ("mysql", Conn())

    import app.services.integration_runtime as ir

    monkeypatch.setattr(ir, "list_columns", _fake_list_columns)
    monkeypatch.setattr(ir, "open_connection", _fake_open_connection)

    table = db.query(MetaTable).filter(MetaTable.id == 100).one()
    ds = db.query(DataSource).filter(DataSource.id == 10).one()
    n = _sync_table_schema(db, table, ds)
    assert n == 2
    cols = (
        db.query(MetaColumn)
        .filter(MetaColumn.table_id == 100)
        .order_by(MetaColumn.ordinal_position)
        .all()
    )
    assert [(c.column_name, c.column_comment, c.is_primary_key) for c in cols] == [
        ("order_id", "订单ID", True),
        ("amount", "订单金额", False),
    ]
    db.refresh(table)
    assert table.table_comment == "订单宽表"
    assert table.row_count == 128000
    assert any("TABLE_ROWS" in s for s in executed)
    assert not any("COUNT(*)" in s.upper().replace(" ", "") for s in executed)


def test_sync_table_schema_source_has_no_count_star():
    """静态守卫：同步实现不得对业务表 SELECT COUNT(*)。"""
    import inspect

    src = inspect.getsource(datamap_api._sync_table_schema)
    assert "COUNT(*)" not in src or "禁止对业务表 SELECT COUNT(*)" in src
    # 实现体里不应再执行 COUNT
    body = src.split('"""', 2)[-1] if '"""' in src else src
    assert "COUNT(*)" not in body

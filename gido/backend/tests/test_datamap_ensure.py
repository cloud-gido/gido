# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据地图 ensure-table 幂等收录。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-datamap-ensure")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.workspace import DataSource, MetaColumn, MetaTable, User, Workspace
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.api.datamap import _ensure_meta_table, _find_meta_table


@pytest.fixture()
def db(monkeypatch):
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
    s.commit()

    def _fake_sync(_db, table, _ds):
        if _db.query(MetaColumn).filter(MetaColumn.table_id == table.id).count():
            return 0
        _db.add(
            MetaColumn(
                table_id=table.id,
                column_name="id",
                column_type="bigint",
                is_nullable=False,
                is_primary_key=True,
                ordinal_position=1,
            )
        )
        _db.commit()
        return 1

    monkeypatch.setattr("app.api.datamap._sync_table_schema", _fake_sync)
    yield s
    s.close()


def test_ensure_creates_then_idempotent(db):
    t1, _ds, created1, synced1, warn1 = _ensure_meta_table(
        db,
        workspace_id=1,
        datasource_id=10,
        table_name="ads_orders",
        db_name="bigdata_dw",
        table_comment="订单",
    )
    assert created1 is True
    assert warn1 is None
    assert synced1 == 1
    assert t1.id is not None
    assert db.query(MetaTable).count() == 1
    assert db.query(MetaColumn).filter(MetaColumn.table_id == t1.id).count() == 1

    t2, _ds2, created2, synced2, warn2 = _ensure_meta_table(
        db,
        workspace_id=1,
        datasource_id=10,
        table_name="ads_orders",
        db_name="bigdata_dw",
        sync_if_empty=True,
    )
    assert created2 is False
    assert t2.id == t1.id
    assert synced2 == 0  # 已有字段，不再同步
    assert warn2 is None
    assert db.query(MetaTable).count() == 1


def test_find_meta_table_normalizes_empty_db_name(db):
    db.add(
        MetaTable(
            workspace_id=1,
            datasource_id=10,
            db_name="bigdata_dw",
            table_name="t1",
        )
    )
    db.commit()
    # 请求未带 db_name 时走数据源默认库
    found = _find_meta_table(
        db,
        workspace_id=1,
        datasource_id=10,
        db_name="bigdata_dw",
        table_name="t1",
    )
    assert found is not None
    assert found.table_name == "t1"

    t, _ds, created, _synced, _w = _ensure_meta_table(
        db,
        workspace_id=1,
        datasource_id=10,
        table_name="t1",
        db_name=None,  # 归一到 bigdata_dw
        sync_if_empty=False,
    )
    assert created is False
    assert t.id == found.id

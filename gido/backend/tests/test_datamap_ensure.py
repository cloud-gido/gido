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
import app.api.streaming  # noqa: F401 — 注册实时作业表，供血缘测试建表


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


def test_sql_lineage_anchors_tables_without_prior_registration(db):
    from app.models.workspace import Lineage, TaskNode
    from app.services.lineage import auto_parse_lineage

    node = TaskNode(
        id=7,
        workspace_id=1,
        name="load_ads",
        node_type="SQL",
        datasource_id=10,
        script_content="INSERT INTO ads_out SELECT id FROM dwd_in",
        created_by=1,
    )
    db.add(node)
    db.commit()
    auto_parse_lineage(node, db)
    names = {row.table_name for row in db.query(MetaTable).all()}
    assert {"ads_out", "dwd_in"} <= names
    assert db.query(Lineage).filter(Lineage.task_node_id == 7).count() == 1
    auto_parse_lineage(node, db)
    assert db.query(MetaTable).filter(MetaTable.table_name.in_(["ads_out", "dwd_in"])).count() == 2
    assert db.query(Lineage).filter(Lineage.task_node_id == 7).count() == 1


def test_lineage_keeps_statements_and_qualified_names_apart():
    from app.services.lineage import _statement_edges

    sql = """
    INSERT OVERWRITE TABLE `bigdata_ads`.`ads_out`
    SELECT * FROM `bigdata_dw`.`dwd_in`;
    INSERT INTO other SELECT * FROM ads_out
    """
    edges = _statement_edges(sql)
    assert ("bigdata_ads", "ads_out", [("bigdata_dw", "dwd_in")]) in edges
    other = [item for item in edges if item[1] == "other"]
    assert other == [(None, "other", [(None, "ads_out")])]


def test_refresh_lineage_uses_saved_sql_without_a_run(db):
    from app.models.workspace import Lineage, TaskNode
    from app.services.lineage import refresh_lineage_for_table

    table = MetaTable(
        workspace_id=1,
        datasource_id=10,
        db_name="bigdata_dw",
        table_name="dwd_src",
        table_type="table",
    )
    node = TaskNode(
        id=8,
        workspace_id=1,
        name="build_ads",
        node_type="SQL",
        datasource_id=10,
        script_content="INSERT INTO ads_dst SELECT id FROM dwd_src",
        created_by=1,
    )
    db.add(table)
    db.add(node)
    db.commit()
    refresh_lineage_for_table(db, table)
    edge = db.query(Lineage).filter(Lineage.task_node_id == 8).one()
    src = db.query(MetaTable).filter(MetaTable.id == edge.src_table_id).one()
    dst = db.query(MetaTable).filter(MetaTable.id == edge.dst_table_id).one()
    assert src.table_name == "dwd_src"
    assert dst.table_name == "ads_dst"


def test_table_name_matching_ignores_qualifiers_and_substrings():
    from app.api.datamap import _leaf_table_name, _sql_mentions_table

    assert _leaf_table_name("`bigdata_dw`.`ads_orders`") == "ads_orders"
    assert _leaf_table_name("bigdata_ads.ads_orders") == "ads_orders"
    assert _sql_mentions_table("SELECT * FROM ads_orders WHERE id = 1", "ads_orders")
    assert not _sql_mentions_table("SELECT * FROM ads_orders_ext", "ads_orders")


def test_schema_sync_does_not_own_business_description():
    import inspect

    from app.api.datamap import _sync_table_schema

    assert "business_description" not in inspect.getsource(_sync_table_schema)


def test_select_sql_quotes_identifiers_and_rejects_injection():
    import pytest

    from app.api.datamap import build_select_sql

    assert build_select_sql("doris", "bigdata_ads", "ads_orders", 100) == (
        "SELECT *\nFROM `bigdata_ads`.`ads_orders`\nLIMIT 100"
    )
    assert 'LIMIT 1000' in build_select_sql("mysql", "db", "t", 99999)
    with pytest.raises(ValueError):
        build_select_sql("doris", "bigdata_ads", "ads_orders`; DROP TABLE t", 10)


def test_column_search_annotates_or_adds_tables():
    from app.api.datamap import merge_column_hits

    rows = [{
        "datasource_id": 10,
        "catalog": "bigdata_ads",
        "table_name": "ads_orders",
        "table_comment": "订单",
    }]
    merge_column_hits(
        rows,
        [
            {"catalog": "bigdata_ads", "table_name": "ads_orders", "column_name": "user_id", "column_comment": "用户"},
            {"catalog": "bigdata_dw", "table_name": "dwd_pay", "column_name": "user_id", "column_comment": ""},
        ],
        datasource_id=10,
        datasource_name="doris",
        ds_type="doris",
        meta_by_key={},
    )
    assert rows[0]["match_columns"] == ["user_id（用户）"]
    assert rows[1]["table_name"] == "dwd_pay"
    assert rows[1]["match_columns"] == ["user_id"]


def test_partition_column_names_from_expression():
    from app.api.datamap import partition_column_names

    assert partition_column_names({"expression": "`dt`, hour"}) == ["dt", "hour"]
    assert partition_column_names(None) == []


def test_sync_lineage_uses_explicit_ends(db):
    from app.models.workspace import Lineage, MetaTable, SyncTask
    from app.services.lineage import refresh_lineage_for_table

    src = MetaTable(workspace_id=1, datasource_id=10, db_name="ods", table_name="ods_orders", table_type="table")
    dst = MetaTable(workspace_id=1, datasource_id=10, db_name="bigdata_dw", table_name="dwd_orders", table_type="table")
    task = SyncTask(
        id=3,
        workspace_id=1,
        name="sync_orders",
        src_datasource_id=10,
        dst_datasource_id=10,
        src_table="ods.ods_orders",
        dst_table="bigdata_dw.dwd_orders",
        created_by=1,
    )
    db.add_all([src, dst, task])
    db.commit()
    refresh_lineage_for_table(db, dst)
    edge = db.query(Lineage).filter(Lineage.sync_task_id == 3).one()
    assert edge.src_table_id == src.id
    assert edge.dst_table_id == dst.id
    assert edge.lineage_type == "sync"


def test_stream_lineage_does_not_invent_unknown_topics(db):
    from app.api.streaming import StreamingJob
    from app.models.workspace import Lineage, MetaTable
    from app.services.lineage import refresh_lineage_for_table, stream_jobs_for_table

    table = MetaTable(workspace_id=1, datasource_id=10, db_name="bigdata_dw", table_name="dwd_orders", table_type="table")
    job = StreamingJob(
        id=9,
        workspace_id=1,
        name="cdc_orders",
        job_type="SQL",
        script_content="INSERT INTO dwd_orders SELECT id FROM kafka_orders",
        created_by=1,
    )
    db.add_all([table, job])
    db.commit()
    refresh_lineage_for_table(db, table)
    assert db.query(MetaTable).filter(MetaTable.table_name == "kafka_orders").count() == 0
    assert db.query(Lineage).filter(Lineage.stream_job_id == 9).count() == 0
    listed = stream_jobs_for_table(db, table)
    assert listed[0]["name"] == "cdc_orders"
    assert listed[0]["role"] == "写入"


def test_partition_summary_keeps_latest_names():
    from app.api.datamap import _partition_summary

    rows = [(f"p{i}", "RANGE", "dt", str(i)) for i in range(10)]
    summary = _partition_summary(rows)
    assert summary["count"] == 10
    assert summary["expression"] == "dt"
    assert summary["names"] == [f"p{i}" for i in range(2, 10)]
    assert _partition_summary([(None, None, None, None)]) is None

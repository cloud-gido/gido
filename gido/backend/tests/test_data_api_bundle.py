# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务 API 配置包导出/导入契约。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-api-bundle")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.data_service import DataApi, DataApiParam
from app.models.workspace import DataSource, User, Workspace
from app.services.data_api_bundle import (
    BUNDLE_FORMAT,
    BUNDLE_SCHEMA_VERSION,
    export_api_bundle,
    import_api_bundle,
)
import app.models.rbac_models  # noqa: F401


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    ws = Workspace(id=1, name="ws")
    user = User(id=1, username="u1", email="u1@test.local", hashed_password="x", is_active=True)
    ds = DataSource(id=10, workspace_id=1, name="test_doris", ds_type="doris", host="h", port=9030)
    ds2 = DataSource(id=11, workspace_id=1, name="prod_doris", ds_type="doris", host="h2", port=9030)
    session.add_all([ws, user, ds, ds2])
    session.commit()
    yield session
    session.close()


def _seed_api(db, *, code="get_orders", status="online", ds_id=10):
    api = DataApi(
        workspace_id=1,
        api_code=code,
        name="Orders",
        mode="sql",
        http_method="GET",
        status=status,
        version=3,
        datasource_id=ds_id,
        sql_template="SELECT * FROM orders WHERE id = :id",
        pagination_enabled=True,
        page_size_default=20,
        page_size_max=100,
        timeout_seconds=30,
        cache_ttl_seconds=0,
        max_rows=5000,
        created_by=1,
    )
    db.add(api)
    db.flush()
    db.add(
        DataApiParam(
            api_id=api.id,
            name="id",
            param_in="query",
            data_type="string",
            required=True,
            sort_order=0,
        )
    )
    db.commit()
    db.refresh(api)
    return api


def test_export_bundle_shape(db):
    api = _seed_api(db)
    bundle = export_api_bundle(db, workspace_id=1, api_ids=[api.id])
    assert bundle["format"] == BUNDLE_FORMAT
    assert bundle["schema_version"] == BUNDLE_SCHEMA_VERSION
    assert len(bundle["apis"]) == 1
    item = bundle["apis"][0]
    assert item["api_code"] == "get_orders"
    assert item["datasource_ref"]["value"] == "test_doris"
    assert "id" not in item
    assert item["params"][0]["name"] == "id"
    assert item["source_status"] == "online"


def test_import_creates_draft(db):
    src = _seed_api(db)
    bundle = export_api_bundle(db, workspace_id=1, api_ids=[src.id])
    # 模拟迁到新 code / 同空间新建
    bundle["apis"][0]["api_code"] = "get_orders_v2"
    bundle["apis"][0]["name"] = "Orders V2"
    res = import_api_bundle(db, workspace_id=1, bundle=bundle, user_id=1)
    assert len(res["created"]) == 1
    created = db.query(DataApi).filter(DataApi.api_code == "get_orders_v2").one()
    assert created.status == "draft"
    assert created.datasource_id == 10
    assert len(created.params) == 1


def test_import_overwrite_online_stages_pending(db):
    """已上线覆盖：不停服，挂 pending，发布后才切配置。"""
    api = _seed_api(db, status="online")
    old_sql = api.sql_template
    bundle = export_api_bundle(db, workspace_id=1, api_ids=[api.id])
    bundle["apis"][0]["sql_template"] = "SELECT 1 AS x"
    bundle["apis"][0]["name"] = "Orders Updated"
    res = import_api_bundle(db, workspace_id=1, bundle=bundle, user_id=1, on_conflict="overwrite")
    assert len(res["updated"]) == 1
    assert res["updated"][0]["action"] == "staged_pending"
    db.refresh(api)
    assert api.status == "online"
    assert api.sql_template == old_sql
    assert api.name == "Orders"
    assert api.pending_definition
    assert api.pending_definition["definition"]["sql_template"] == "SELECT 1 AS x"

    from app.services.data_service_publish import execute_data_api_publish
    from types import SimpleNamespace

    out = execute_data_api_publish(db, api, SimpleNamespace(id=1))
    assert out["applied_pending"] is True
    db.refresh(api)
    assert api.status == "online"
    assert api.name == "Orders Updated"
    assert "SELECT 1" in (api.sql_template or "")
    assert api.pending_definition is None


def test_import_overwrite_draft_applies_directly(db):
    api = _seed_api(db, status="draft")
    bundle = export_api_bundle(db, workspace_id=1, api_ids=[api.id])
    bundle["apis"][0]["name"] = "Draft Updated"
    res = import_api_bundle(db, workspace_id=1, bundle=bundle, user_id=1, on_conflict="overwrite")
    assert res["updated"][0]["action"] == "updated_draft"
    db.refresh(api)
    assert api.status == "draft"
    assert api.name == "Draft Updated"
    assert not api.pending_definition


def test_import_skip_and_auto_datasource_by_type(db):
    api = _seed_api(db)
    bundle = export_api_bundle(db, workspace_id=1, api_ids=[api.id])
    res = import_api_bundle(db, workspace_id=1, bundle=bundle, user_id=1, on_conflict="skip")
    assert len(res["skipped"]) == 1

    # 改名导出侧数据源引用，生产只留 prod_doris（同类型唯一）应自动匹配
    bundle["apis"][0]["api_code"] = "mapped_api"
    bundle["apis"][0]["datasource_ref"] = {"by": "name", "value": "test_doris", "ds_type": "doris"}
    # 删掉同名 test_doris，只留 prod
    db.query(DataSource).filter(DataSource.id == 10).delete()
    db.commit()
    res2 = import_api_bundle(db, workspace_id=1, bundle=bundle, user_id=1)
    created = db.query(DataApi).filter(DataApi.api_code == "mapped_api").one()
    assert created.datasource_id == 11


def test_import_fail_on_conflict(db):
    api = _seed_api(db)
    bundle = export_api_bundle(db, workspace_id=1, api_ids=[api.id])
    with pytest.raises(HTTPException) as ei:
        import_api_bundle(db, workspace_id=1, bundle=bundle, user_id=1, on_conflict="fail")
    assert ei.value.status_code == 409


def test_reject_future_schema(db):
    with pytest.raises(HTTPException) as ei:
        import_api_bundle(
            db,
            workspace_id=1,
            bundle={"format": BUNDLE_FORMAT, "schema_version": 99, "apis": [{"api_code": "a"}]},
            user_id=1,
        )
    assert ei.value.status_code == 400

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""返回字段契约：HTTP 集成（内存 SQLite + TestClient，不打真实数仓）。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-data-api-contract")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.data_service import ConsumerApp, ConsumerAppApiGrant, DataApi, DataApiParam
from app.models.workspace import DataSource, User, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds_models  # noqa: F401
from app.api import auth, data_service
from app.api.data_service_open import open_router


def _fake_execute(db, api, ds, raw_params, page_no=1, page_size=None, skip_cache=False):
    return {
        "list": [{"player_id": "p1", "amount": 10}],
        "TotalCount": 1,
        "PageNumber": int(page_no or 1),
        "PageSize": int(page_size or 20),
        "truncated": False,
        "cache_hit": False,
        "__gido_columns__": ["player_id", "amount"],
    }


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    run_rbac_bootstrap(db)
    admin = User(
        username="admin",
        email="admin@gido.com",
        full_name="管理员",
        hashed_password=get_password_hash("admin123"),
        is_admin=True,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    ws = Workspace(name="infras", description="default", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=admin.id, role="admin"))
    ds = DataSource(
        workspace_id=ws.id,
        name="test_doris",
        ds_type="doris",
        host="127.0.0.1",
        port=9030,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    api = DataApi(
        workspace_id=ws.id,
        api_code="demo_api",
        name="Demo",
        mode="sql",
        http_method="GET",
        status="online",
        version=1,
        datasource_id=ds.id,
        sql_template="SELECT player_id, amount FROM t",
        response_fields=None,
        pagination_enabled=True,
        page_size_default=20,
        page_size_max=100,
        timeout_seconds=30,
        cache_ttl_seconds=0,
        max_rows=5000,
        created_by=admin.id,
    )
    db.add(api)
    db.flush()
    db.add(
        DataApiParam(
            api_id=api.id,
            name="fixture_id",
            param_in="query",
            data_type="string",
            required=False,
            sort_order=0,
        )
    )
    secret_plain = "test-app-secret-plain"
    app_row = ConsumerApp(
        workspace_id=ws.id,
        name="ops",
        app_key="ak_demo_ops",
        app_secret_hash=get_password_hash(secret_plain),
        ip_whitelist=None,
        qps_limit=1000,
        is_active=True,
        created_by=admin.id,
    )
    db.add(app_row)
    db.flush()
    db.add(ConsumerAppApiGrant(app_id=app_row.id, api_id=api.id))
    db.commit()
    run_rbac_bootstrap(db)
    api_id = api.id
    ws_id = ws.id
    db.close()

    monkeypatch.setattr("app.api.data_service_open.execute_data_api", _fake_execute)
    monkeypatch.setattr("app.api.data_service.execute_data_api", _fake_execute)

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(data_service.router, prefix="/api")
    app.include_router(open_router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        c.SessionLocal = SessionLocal  # type: ignore[attr-defined]
        c.api_id = api_id  # type: ignore[attr-defined]
        c.ws_id = ws_id  # type: ignore[attr-defined]
        c.app_key = "ak_demo_ops"  # type: ignore[attr-defined]
        c.app_secret = secret_plain  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _login(c: TestClient) -> str:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_open_gateway_strips_internal_columns_and_persists_contract(client: TestClient):
    r = client.get(
        f"/api/open/v1/ws/{client.ws_id}/demo_api",
        headers={"X-App-Key": client.app_key, "X-App-Secret": client.app_secret},
        params={"fixture_id": "FX1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["code"] == 0
    assert set(body.keys()) == {"code", "success", "message", "trace_id", "data"}
    assert "__gido_columns__" not in body
    assert "__gido_columns__" not in (body.get("data") or {})
    assert body["data"]["list"] == [{"player_id": "p1", "amount": 10}]
    assert "TotalCount" in body["data"]

    db = client.SessionLocal()
    try:
        api = db.query(DataApi).filter(DataApi.id == client.api_id).one()
        names = [x.get("name") for x in (api.response_fields or [])]
        assert names == ["player_id", "amount"]
    finally:
        db.close()


def test_contract_and_openapi_expose_response_fields(client: TestClient):
    # 先跑开放网关写入契约
    r0 = client.get(
        f"/api/open/v1/ws/{client.ws_id}/demo_api",
        headers={"X-App-Key": client.app_key, "X-App-Secret": client.app_secret},
    )
    assert r0.status_code == 200, r0.text

    token = _login(client)
    r = client.get(f"/api/data-service/apis/{client.api_id}/contract", headers=_auth(token))
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["api_code"] == "demo_api"
    assert c["public_open_path"] == f"/api/open/v1/ws/{client.ws_id}/demo_api"
    assert c["response_field_names"] == ["player_id", "amount"]
    assert c["has_response_contract"] is True
    assert c["params"][0]["name"] == "fixture_id"

    oa = client.get(f"/api/data-service/apis/{client.api_id}/openapi", headers=_auth(token))
    assert oa.status_code == 200, oa.text
    path = f"/open/v1/ws/{client.ws_id}/demo_api"
    items = oa.json()["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"][
        "properties"
    ]["data"]["properties"]["list"]["items"]
    assert items["properties"]["player_id"]["type"] == "string"
    assert items["properties"]["amount"]["type"] == "string"


def test_console_test_also_strips_internal_marker(client: TestClient):
    token = _login(client)
    r = client.post(
        f"/api/data-service/apis/{client.api_id}/test",
        headers=_auth(token),
        json={"params": {"fixture_id": "FX1"}, "page": 1, "page_size": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert "__gido_columns__" not in body
    assert "__gido_columns__" not in (body.get("data") or {})
    assert body["data"]["list"][0]["player_id"] == "p1"

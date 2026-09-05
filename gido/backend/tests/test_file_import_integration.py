# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地文件导入：API 集成测试（SQLite + 打桩装载，不连真实 Doris/MySQL）。"""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-file-import")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import get_password_hash
from app.models.workspace import DataSource, SyncTask, User, Workspace, WorkspaceMember
from app.services.rbac_seed import run_rbac_bootstrap
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.api import auth, workspace, integration


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_IMPORT_UPLOAD_DIR", str(tmp_path / "imports"))
    monkeypatch.setattr(
        "app.core.config.settings.FILE_IMPORT_UPLOAD_DIR",
        str(tmp_path / "imports"),
    )
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_MAX_BYTES", 3 * 1024 * 1024 * 1024)
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_MAX_ROWS", 5_000_000)
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_XLSX_MAX_BYTES", 200 * 1024 * 1024)

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
    ws0 = Workspace(name="infras", description="default", owner_id=admin.id, timezone="Asia/Shanghai")
    db.add(ws0)
    db.commit()
    db.refresh(ws0)
    db.add(WorkspaceMember(workspace_id=ws0.id, user_id=admin.id, role="admin"))
    db.commit()
    run_rbac_bootstrap(db)

    ds = DataSource(
        workspace_id=ws0.id,
        name="doris_demo",
        ds_type="doris",
        host="127.0.0.1",
        port=9030,
        database="demo",
        username="root",
        password="",
        is_active=True,
        created_by=admin.id,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    ds_id = ds.id
    ws_id = ws0.id
    db.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.include_router(workspace.router, prefix="/api")
    app.include_router(integration.router, prefix="/api")
    app.dependency_overrides[get_db] = _get_db

    with TestClient(app) as c:
        yield c, SessionLocal, ws_id, ds_id
    app.dependency_overrides.clear()


def _login(c: TestClient) -> str:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_file_import_upload_preview_create_task(client):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    csv_bytes = b"id,name,amount\n1,alice,1.5\n2,bob,2.0\n"

    up = c.post(
        "/api/integration/file-import/upload",
        headers=h,
        data={
            "workspace_id": str(ws_id),
            "has_header": "true",
        },
        files={"file": ("orders.csv", BytesIO(csv_bytes), "text/csv")},
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["file_id"]
    assert body["row_count"] == 2
    assert body["format"] == "csv"
    assert body["max_bytes"] >= 2 * 1024 * 1024 * 1024
    assert body["max_rows"] >= 3_000_000
    assert len(body["columns"]) == 3
    file_id = body["file_id"]

    prev = c.post(
        "/api/integration/file-import/preview",
        headers=h,
        json={
            "workspace_id": ws_id,
            "file_id": file_id,
            "has_header": True,
            "delimiter": ",",
        },
    )
    assert prev.status_code == 200, prev.text
    assert prev.json()["row_count"] == 2

    with patch("app.services.file_import_exec.table_exists", return_value=False):
        ddl = c.post(
            "/api/integration/file-import/preview-ddl",
            headers=h,
            json={
                "datasource_id": ds_id,
                "table_name": "import_orders",
                "columns": body["columns"],
            },
        )
    assert ddl.status_code == 200, ddl.text
    assert "CREATE TABLE" in ddl.json()["ddl"]
    assert ddl.json()["table_exists"] is False

    with patch("app.services.file_import_exec.table_exists", return_value=False):
        created = c.post(
            "/api/integration/file-import/tasks",
            headers=h,
            json={
                "workspace_id": ws_id,
                "name": "导入订单",
                "dst_datasource_id": ds_id,
                "dst_table": "import_orders",
                "file_id": file_id,
                "columns": [
                    {"name": c0["name"], "type": c0["type"], "nullable": True, "is_primary_key": i == 0}
                    for i, c0 in enumerate(body["columns"])
                ],
                "has_header": True,
                "delimiter": ",",
                "register_datamap": False,
                "if_exists": "fail",
                "run_now": False,
            },
        )
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    assert task["sync_mode"] == "file_import"
    assert task["dst_table"] == "import_orders"
    assert created.json()["record_id"] is None

    tasks = c.get("/api/integration/tasks", headers=h, params={"workspace_id": ws_id})
    assert tasks.status_code == 200
    assert any(t["id"] == task["id"] and t["sync_mode"] == "file_import" for t in tasks.json())


def test_file_import_run_uses_execute_hook(client):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    csv_bytes = b"id,name\n1,a\n2,b\n"
    up = c.post(
        "/api/integration/file-import/upload",
        headers=h,
        data={"workspace_id": str(ws_id), "has_header": "true"},
        files={"file": ("t.csv", BytesIO(csv_bytes), "text/csv")},
    )
    assert up.status_code == 200
    file_id = up.json()["file_id"]
    cols = up.json()["columns"]

    with patch("app.services.file_import_exec.table_exists", return_value=False):
        created = c.post(
            "/api/integration/file-import/tasks",
            headers=h,
            json={
                "workspace_id": ws_id,
                "name": "run_once",
                "dst_datasource_id": ds_id,
                "dst_table": "t_run",
                "file_id": file_id,
                "columns": [
                    {"name": x["name"], "type": x["type"], "nullable": True, "is_primary_key": False}
                    for x in cols
                ],
                "has_header": True,
                "run_now": False,
                "register_datamap": False,
            },
        )
    assert created.status_code == 200, created.text
    task_id = created.json()["task"]["id"]

    with patch("app.api.integration.start_sync_async") as start:
        fake_rec = MagicMock()
        fake_rec.id = 99
        fake_rec.status = "running"
        start.return_value = fake_rec
        run = c.post(f"/api/integration/tasks/{task_id}/run", headers=h)
        assert run.status_code == 200, run.text
        assert run.json()["record_id"] == 99
        start.assert_called_once()


def test_file_import_reject_existing_table(client):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    up = c.post(
        "/api/integration/file-import/upload",
        headers=h,
        data={"workspace_id": str(ws_id), "has_header": "true"},
        files={"file": ("t.csv", BytesIO(b"id\n1\n"), "text/csv")},
    )
    file_id = up.json()["file_id"]
    cols = up.json()["columns"]
    with patch("app.services.file_import_exec.table_exists", return_value=True):
        created = c.post(
            "/api/integration/file-import/tasks",
            headers=h,
            json={
                "workspace_id": ws_id,
                "name": "dup",
                "dst_datasource_id": ds_id,
                "dst_table": "exists_tbl",
                "file_id": file_id,
                "columns": [{"name": x["name"], "type": "bigint", "nullable": True, "is_primary_key": False} for x in cols],
                "if_exists": "fail",
                "run_now": False,
            },
        )
    assert created.status_code == 400
    assert "已存在" in created.json()["detail"]


def test_file_import_rejects_oversized_xlsx(client, monkeypatch, tmp_path):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_XLSX_MAX_BYTES", 100)
    big = b"PK" + b"0" * 200  # fake xlsx name with oversize
    up = c.post(
        "/api/integration/file-import/upload",
        headers=h,
        data={"workspace_id": str(ws_id)},
        files={"file": ("big.xlsx", BytesIO(big), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up.status_code == 400
    assert "上限" in up.json()["detail"]


def _create_file_import_task(c, h, ws_id, ds_id, name="fi_task"):
    up = c.post(
        "/api/integration/file-import/upload",
        headers=h,
        data={"workspace_id": str(ws_id), "has_header": "true"},
        files={"file": ("t.csv", BytesIO(b"id,name\n1,a\n"), "text/csv")},
    )
    assert up.status_code == 200, up.text
    file_id = up.json()["file_id"]
    cols = [
        {"name": x["name"], "type": x["type"], "nullable": True, "is_primary_key": False}
        for x in up.json()["columns"]
    ]
    with patch("app.services.file_import_exec.table_exists", return_value=False):
        created = c.post(
            "/api/integration/file-import/tasks",
            headers=h,
            json={
                "workspace_id": ws_id,
                "name": name,
                "dst_datasource_id": ds_id,
                "dst_table": f"t_{name}",
                "file_id": file_id,
                "columns": cols,
                "has_header": True,
                "run_now": False,
                "register_datamap": False,
                "operation_mode": "create",
            },
        )
    assert created.status_code == 200, created.text
    return created.json(), file_id, cols


def test_file_import_block_sync_config_update(client):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    body, _, _ = _create_file_import_task(c, h, ws_id, ds_id, "block_cfg")
    task_id = body["task"]["id"]
    upd = c.put(
        f"/api/integration/tasks/{task_id}",
        headers=h,
        json={"sync_config": {"file_id": "hijack"}},
    )
    assert upd.status_code == 400
    assert "versions" in upd.json()["detail"]


def test_file_import_schema_diff_endpoint(client):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    cols = [
        {"name": "id", "type": "bigint", "nullable": False, "is_primary_key": True},
        {"name": "name", "type": "string", "nullable": True, "is_primary_key": False},
    ]
    with patch("app.services.file_import_exec.table_exists", return_value=True), patch(
        "app.services.integration_runtime.list_columns",
        return_value=[{"name": "id", "type": "bigint"}, {"name": "name", "type": "string"}],
    ):
        r = c.post(
            "/api/integration/file-import/schema-diff",
            headers=h,
            json={"datasource_id": ds_id, "table_name": "t1", "columns": cols},
        )
    assert r.status_code == 200, r.text
    assert r.json()["table_exists"] is True
    assert r.json()["diff"]["compatible"] is True


def test_file_import_versions_list_and_create(client):
    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    body, file_id, cols = _create_file_import_task(c, h, ws_id, ds_id, "ver1")
    task_id = body["task"]["id"]

    listed = c.get(f"/api/integration/file-import/tasks/{task_id}/versions", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)
    assert len(listed.json()) >= 1

    # 再传一份文件建新版本（目标表已存在 → append）
    up2 = c.post(
        "/api/integration/file-import/upload",
        headers=h,
        data={"workspace_id": str(ws_id), "has_header": "true"},
        files={"file": ("t2.csv", BytesIO(b"id,name\n2,b\n"), "text/csv")},
    )
    fid2 = up2.json()["file_id"]
    with patch("app.services.file_import_exec.table_exists", return_value=True), patch(
        "app.services.integration_runtime.list_columns",
        return_value=[{"name": c["name"], "type": c["type"]} for c in cols],
    ):
        ver = c.post(
            f"/api/integration/file-import/tasks/{task_id}/versions",
            headers=h,
            json={
                "file_id": fid2,
                "columns": cols,
                "operation_mode": "append",
                "quality_mode": "strict",
                "activate": True,
                "run_now": False,
            },
        )
    assert ver.status_code == 200, ver.text
    assert ver.json()["version"]["operation_mode"] == "append"
    assert ver.json()["version"]["file_id"] == fid2


def test_file_import_run_blocks_after_success_and_failed(client):
    from app.models.workspace import SyncRecord

    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    body, _, _ = _create_file_import_task(c, h, ws_id, ds_id, "run_gate")
    task_id = body["task"]["id"]

    db = SessionLocal()
    db.add(
        SyncRecord(
            sync_task_id=task_id,
            status="success",
            trigger_type="manual",
            execution_key="ek-ok",
            started_at=__import__("datetime").datetime.utcnow(),
        )
    )
    db.commit()
    db.close()

    blocked = c.post(f"/api/integration/tasks/{task_id}/run", headers=h)
    assert blocked.status_code == 400
    assert "不可直接重跑" in blocked.json()["detail"]

    db = SessionLocal()
    db.query(SyncRecord).filter(SyncRecord.sync_task_id == task_id).delete()
    db.add(
        SyncRecord(
            sync_task_id=task_id,
            status="failed",
            trigger_type="manual",
            execution_key="ek-fail",
            started_at=__import__("datetime").datetime.utcnow(),
        )
    )
    db.commit()
    db.close()

    blocked2 = c.post(f"/api/integration/tasks/{task_id}/run", headers=h)
    assert blocked2.status_code == 400
    assert "retry" in blocked2.json()["detail"]


def test_file_import_idempotent_retry(client):
    from app.models.workspace import SyncRecord

    c, SessionLocal, ws_id, ds_id = client
    token = _login(c)
    h = _auth(token)
    body, _, _ = _create_file_import_task(c, h, ws_id, ds_id, "retry1")
    task_id = body["task"]["id"]

    db = SessionLocal()
    rec = SyncRecord(
        sync_task_id=task_id,
        status="failed",
        trigger_type="manual",
        execution_key="ek-retry-same",
        started_at=__import__("datetime").datetime.utcnow(),
        error_msg="boom",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    rid = rec.id
    db.close()

    with patch("app.api.integration.start_sync_async") as start:
        fake = MagicMock()
        fake.id = 501
        fake.execution_key = "ek-retry-same"
        start.return_value = fake
        r = c.post(f"/api/integration/file-import/records/{rid}/retry", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["execution_key"] == "ek-retry-same"
        assert r.json()["retry_of"] == rid
        start.assert_called_once()
        kwargs = start.call_args.kwargs
        assert kwargs.get("execution_key") == "ek-retry-same"
        assert kwargs.get("retry_of") == rid
        assert kwargs.get("trigger_type") == "retry"

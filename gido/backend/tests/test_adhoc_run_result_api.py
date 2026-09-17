# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import adhoc_runs
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.workspace import (
    AdhocRun,
    AdhocRunExport,
    AdhocRunLogChunk,
    AdhocRunResultChunk,
    AdhocRunShare,
    AdhocRunShareGrant,
    AdhocRunStatement,
    DataSource,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services import adhoc_run_export as export_service


@pytest.fixture()
def api(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = factory()
    owner = User(
        username="owner",
        email="owner@example.com",
        hashed_password="x",
        is_active=True,
        is_admin=True,
    )
    outsider = User(
        username="outsider",
        email="outsider@example.com",
        hashed_password="x",
        is_active=True,
    )
    db.add_all([owner, outsider])
    db.flush()
    workspace = Workspace(name="adhoc-api", owner_id=owner.id)
    db.add(workspace)
    db.flush()
    db.add_all(
        [
            WorkspaceMember(workspace_id=workspace.id, user_id=owner.id, role="admin"),
            WorkspaceMember(
                workspace_id=workspace.id, user_id=outsider.id, role="developer"
            ),
        ]
    )
    datasource = DataSource(
        workspace_id=workspace.id,
        name="test-db",
        ds_type="postgresql",
        host="localhost",
        database="test",
    )
    db.add(datasource)
    db.flush()
    run = AdhocRun(
        workspace_id=workspace.id,
        source="studio",
        triggered_by=owner.id,
        status="success",
        status_version=3,
        datasource_id=datasource.id,
    )
    db.add(run)
    db.flush()
    statement = AdhocRunStatement(
        run_id=run.id,
        statement_index=0,
        sql_text="select n, label",
        status="success",
        column_schema={
            "columns": ["n", "label"],
            "column_types": ["int", "text"],
            "fields": [
                {
                    "name": "n",
                    "raw_type": "int",
                    "semantic_type": "number",
                    "nullable": False,
                    "precision": 11,
                    "scale": 0,
                },
                {
                    "name": "label",
                    "raw_type": "text",
                    "semantic_type": "string",
                    "nullable": True,
                    "precision": None,
                    "scale": None,
                },
            ],
        },
        result_rows=3,
        result_chunk_count=2,
    )
    db.add(statement)
    db.flush()
    db.add_all(
        [
            AdhocRunResultChunk(
                statement_id=statement.id,
                chunk_index=0,
                row_offset=0,
                row_count=2,
                payload={"rows": [[1, "a"], [2, "b"]]},
            ),
            AdhocRunResultChunk(
                statement_id=statement.id,
                chunk_index=1,
                row_offset=2,
                row_count=1,
                payload={"rows": [[3, "c"]]},
            ),
            *[
                AdhocRunLogChunk(
                    run_id=run.id, seq=index, stream="stdout", content=f"log-{index}\n"
                )
                for index in range(1, 4)
            ],
        ]
    )
    db.commit()
    ids = {
        "run": run.id,
        "statement": statement.id,
        "owner": owner.id,
        "outsider": outsider.id,
    }
    current = {"id": owner.id}
    db.close()

    def _db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def _user():
        session = factory()
        try:
            yield session.query(User).filter(User.id == current["id"]).one()
        finally:
            session.close()

    app = FastAPI()
    app.include_router(adhoc_runs.router, prefix="/api")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    monkeypatch.setattr(export_service, "SessionLocal", factory)
    with TestClient(app) as client:
        yield client, factory, ids, current


def test_events_drain_terminal_logs_and_increment_statements(api):
    client, _factory, ids, _current = api
    first = client.get(
        f"/api/adhoc-runs/{ids['run']}/events",
        params={"after_log_seq": 0, "log_limit": 2},
    )
    assert first.status_code == 200
    body = first.json()
    assert [item["seq"] for item in body["logs"]["chunks"]] == [1, 2]
    assert body["logs"]["has_more"] is True
    assert body["poll_required"] is True
    assert len(body["statements"]) == 1
    assert body["timing"]["queue_duration_ms"] >= 0
    assert body["timing"]["execution_duration_ms"] is None

    second = client.get(
        f"/api/adhoc-runs/{ids['run']}/events",
        params={
            "after_log_seq": body["logs"]["next_seq"],
            "statement_version": body["statement_version"],
            "log_limit": 2,
        },
    )
    assert second.status_code == 200
    body = second.json()
    assert [item["seq"] for item in body["logs"]["chunks"]] == [3]
    assert body["statements"] == []
    assert body["poll_required"] is False


def test_logs_has_more_is_false_for_exact_page(api):
    client, _factory, ids, _current = api
    response = client.get(
        f"/api/adhoc-runs/{ids['run']}/logs",
        params={"after_seq": 0, "limit": 3},
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["seq"] for item in body["chunks"]] == [1, 2, 3]
    assert body["has_more"] is False


def test_rows_cursor_flattens_chunks_and_checks_permission(api):
    client, _factory, ids, current = api
    first = client.get(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows",
        params={"limit": 2},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["columns"] == ["n", "label"]
    assert body["rows"] == [[1, "a"], [2, "b"]]
    assert body["snapshot"] is True
    assert body["has_more"] is True
    second = client.get(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows",
        params={"cursor": body["next_cursor"], "limit": 2},
    )
    assert second.json()["rows"] == [[3, "c"]]
    assert second.json()["has_more"] is False

    current["id"] = ids["outsider"]
    denied = client.get(f"/api/adhoc-runs/{ids['run']}/statements")
    assert denied.status_code == 403


def test_rows_query_uses_all_chunks_and_binds_cursor(api):
    client, _factory, ids, _current = api
    metadata = client.get(f"/api/adhoc-runs/{ids['run']}/statements").json()
    version = metadata["statements"][0]["statement_version"]
    first = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
        json={
            "search": "",
            "filters": [{"column": "n", "operator": "gte", "value": 2}],
            "sort": [{"column": "n", "direction": "desc"}],
            "limit": 1,
            "statement_version": version,
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["rows"] == [[3, "c"]]
    assert body["total"] == 2
    assert body["source_total"] == 3
    assert body["fields"][0]["semantic_type"] == "number"

    second = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
        json={
            "filters": [{"column": "n", "operator": "gte", "value": 2}],
            "sort": [{"column": "n", "direction": "desc"}],
            "cursor": body["next_cursor"],
            "limit": 1,
            "statement_version": version,
        },
    )
    assert second.status_code == 200
    assert second.json()["rows"] == [[2, "b"]]

    mismatched = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
        json={
            "search": "different",
            "cursor": body["next_cursor"],
            "statement_version": version,
        },
    )
    assert mismatched.status_code == 400


def test_rows_query_supports_progressive_version_bound_results(api):
    client, factory, ids, _current = api
    db = factory()
    try:
        statement = db.query(AdhocRunStatement).filter(
            AdhocRunStatement.run_id == ids["run"],
            AdhocRunStatement.statement_index == 0,
        ).one()
        statement.status = "running"
        db.commit()
    finally:
        db.close()

    metadata = client.get(f"/api/adhoc-runs/{ids['run']}/statements").json()
    version = metadata["statements"][0]["statement_version"]
    response = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
        json={"limit": 2, "statement_version": version},
    )
    assert response.status_code == 200
    assert response.json()["rows"] == [[1, "a"], [2, "b"]]
    assert response.json()["has_more"] is True

    selected = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
        json={
            "filters": [{"column": "n", "operator": "in", "value": [1, 3]}],
            "statement_version": version,
        },
    )
    assert selected.status_code == 200
    assert selected.json()["rows"] == [[1, "a"], [3, "c"]]

    multi_sorted = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
        json={
            "sort": [
                {"column": "label", "direction": "desc"},
                {"column": "n", "direction": "asc"},
            ],
            "statement_version": version,
        },
    )
    assert multi_sorted.status_code == 200
    assert multi_sorted.json()["rows"] == [[3, "c"], [2, "b"], [1, "a"]]


def test_rows_query_rejects_unknown_columns_operators_and_three_sorts(api):
    client, _factory, ids, _current = api
    version = client.get(
        f"/api/adhoc-runs/{ids['run']}/statements"
    ).json()["statements"][0]["statement_version"]
    for payload in (
        {"filters": [{"column": "missing", "operator": "eq", "value": 1}]},
        {"filters": [{"column": "n", "operator": "regex", "value": "1"}]},
        {
            "sort": [
                {"column": "n", "direction": "asc"},
                {"column": "label", "direction": "asc"},
                {"column": "n", "direction": "desc"},
            ]
        },
    ):
        payload["statement_version"] = version
        response = client.post(
            f"/api/adhoc-runs/{ids['run']}/statements/0/rows/query",
            json=payload,
        )
        assert response.status_code == 400


def test_explicit_explain_persists_plan_snapshot(api, monkeypatch):
    from app.services import adhoc_explain

    client, _factory, ids, _current = api

    def _capture(db, *, datasource, statement):
        assert datasource.name == "test-db"
        statement.plan_snapshot = {
            "kind": "explain",
            "analyze": False,
            "rows": [["scan"]],
        }
        db.commit()
        db.refresh(statement)
        return statement.plan_snapshot

    monkeypatch.setattr(adhoc_explain, "capture_statement_plan", _capture)
    response = client.post(
        f"/api/adhoc-runs/{ids['run']}/statements/0/explain"
    )
    assert response.status_code == 200
    assert response.json()["plan_snapshot"]["analyze"] is False
    assert response.json()["statement"]["plan_snapshot"]["rows"] == [["scan"]]


@pytest.mark.parametrize(
    ("export_format", "expected"),
    [
        ("csv", "n,label\r\n1,a\r\n2,b\r\n3,c\r\n"),
        (
            "jsonl",
            '{"n":1,"label":"a"}\n{"n":2,"label":"b"}\n{"n":3,"label":"c"}\n',
        ),
    ],
)
def test_database_fallback_export_formats(api, monkeypatch, export_format, expected):
    client, _factory, ids, _current = api
    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: False)
    created = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": export_format, "statement_index": 0},
    )
    assert created.status_code == 202
    export_id = created.json()["id"]
    export_service.process_export(export_id)
    status = client.get(f"/api/adhoc-runs/{ids['run']}/exports/{export_id}")
    assert status.json()["status"] == "success"
    downloaded = client.get(
        f"/api/adhoc-runs/{ids['run']}/exports/{export_id}/download"
    )
    assert downloaded.status_code == 200
    assert downloaded.content.decode("utf-8-sig") == expected


def test_export_reuses_persisted_query_snapshot(api, monkeypatch):
    client, factory, ids, _current = api
    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: False)
    version = client.get(
        f"/api/adhoc-runs/{ids['run']}/statements"
    ).json()["statements"][0]["statement_version"]
    created = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={
            "format": "csv",
            "statement_index": 0,
            "filters": [{"column": "n", "operator": "gte", "value": 2}],
            "sort": [{"column": "n", "direction": "desc"}],
            "statement_version": version,
        },
    )
    assert created.status_code == 202
    export_id = created.json()["id"]
    export_service.process_export(export_id)
    downloaded = client.get(
        f"/api/adhoc-runs/{ids['run']}/exports/{export_id}/download"
    )
    assert downloaded.content.decode("utf-8-sig") == "n,label\r\n3,c\r\n2,b\r\n"
    db = factory()
    try:
        saved = db.query(AdhocRunExport).filter(AdhocRunExport.id == export_id).one()
        assert saved.statement_version == version
        assert saved.query_spec["filters"][0]["column"] == "n"
        assert saved.row_count == 2
    finally:
        db.close()


@pytest.mark.parametrize("export_format", ["xlsx", "parquet"])
def test_binary_database_fallback_export_formats(api, monkeypatch, export_format):
    if export_format == "parquet":
        pytest.importorskip("pyarrow")
    client, _factory, ids, _current = api
    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: False)
    created = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": export_format, "statement_index": 0},
    ).json()
    export_service.process_export(created["id"])
    downloaded = client.get(
        f"/api/adhoc-runs/{ids['run']}/exports/{created['id']}/download"
    )
    assert downloaded.status_code == 200
    if export_format == "xlsx":
        assert downloaded.content.startswith(b"PK")
    else:
        assert downloaded.content[:4] == b"PAR1"
        assert downloaded.content[-4:] == b"PAR1"


def test_authenticated_share_redeem_grant_and_revoke(api, monkeypatch):
    client, factory, ids, current = api
    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: False)
    export = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": "csv", "statement_index": 0},
    ).json()
    export_service.process_export(export["id"])
    created = client.post(
        f"/api/adhoc-runs/{ids['run']}/shares",
        json={"ttl_hours": 2},
    )
    assert created.status_code == 201
    token = created.json()["token"]
    share_id = created.json()["id"]
    db = factory()
    try:
        share = db.query(AdhocRunShare).filter(AdhocRunShare.id == share_id).one()
        assert share.token_hash != token
        assert len(share.token_hash) == 64
    finally:
        db.close()

    current["id"] = ids["outsider"]
    assert client.get(f"/api/adhoc-runs/{ids['run']}").status_code == 403
    redeemed = client.get(f"/api/adhoc-runs/share-links/{token}")
    assert redeemed.status_code == 200
    assert client.get(f"/api/adhoc-runs/{ids['run']}").status_code == 200
    assert client.post(
        f"/api/adhoc-runs/{ids['run']}/shares",
        json={"ttl_hours": 2},
    ).status_code == 403
    assert (
        client.get(f"/api/adhoc-runs/{ids['run']}/statements").status_code == 200
    )
    assert (
        client.get(
            f"/api/adhoc-runs/{ids['run']}/exports/{export['id']}/download"
        ).status_code
        == 200
    )
    db = factory()
    try:
        assert (
            db.query(AdhocRunShareGrant)
            .filter(
                AdhocRunShareGrant.run_id == ids["run"],
                AdhocRunShareGrant.user_id == ids["outsider"],
            )
            .count()
            == 1
        )
    finally:
        db.close()

    current["id"] = ids["owner"]
    revoked = client.delete(
        f"/api/adhoc-runs/{ids['run']}/shares/{share_id}"
    )
    assert revoked.status_code == 200
    current["id"] = ids["outsider"]
    assert client.get(f"/api/adhoc-runs/{ids['run']}").status_code == 403
    assert client.get(f"/api/adhoc-runs/share-links/{token}").status_code == 410


def test_s3_streaming_upload_cancel_ttl_and_fallback_limit(api, monkeypatch):
    client, factory, ids, current = api
    uploaded = {}
    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: True)

    def _upload(_namespace, filename, path, content_type):
        with open(path, "rb") as handle:
            uploaded[filename] = handle.read()
        uploaded["content_type"] = content_type

    monkeypatch.setattr(export_service.artifact_s3, "put_shared_object_file", _upload)
    created = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": "csv", "statement_index": 0},
    ).json()
    export_service.process_export(created["id"])
    current["id"] = ids["outsider"]
    assert (
        client.get(f"/api/adhoc-runs/{ids['run']}/exports/{created['id']}").status_code
        == 403
    )
    current["id"] = ids["owner"]
    db = factory()
    s3_export = db.query(AdhocRunExport).filter(AdhocRunExport.id == created["id"]).one()
    assert s3_export.status == "success"
    assert uploaded[s3_export.storage_key].startswith(b"\xef\xbb\xbfn,label")

    queued = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": "jsonl", "statement_index": 0},
    ).json()
    cancelled = client.delete(
        f"/api/adhoc-runs/{ids['run']}/exports/{queued['id']}"
    )
    assert cancelled.json()["status"] == "cancelled"

    s3_export.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    db.close()
    monkeypatch.setattr(
        export_service.artifact_s3, "delete_shared_object", lambda *_args: None
    )
    assert export_service.expire_exports() == 1

    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: False)
    monkeypatch.setattr(
        export_service.settings, "ADHOC_EXPORT_DB_FALLBACK_MAX_BYTES", 5
    )
    oversized = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": "csv", "statement_index": 0},
    ).json()
    export_service.process_export(oversized["id"])
    db = factory()
    failed = db.query(AdhocRunExport).filter(AdhocRunExport.id == oversized["id"]).one()
    assert failed.status == "failed"
    assert "超过数据库回退上限" in failed.error_message
    db.close()


def test_export_cancel_during_upload_cannot_be_overwritten(api, monkeypatch):
    client, factory, ids, _current = api
    deleted = []
    monkeypatch.setattr(export_service.artifact_s3, "artifact_s3_enabled", lambda: True)

    def _upload(_namespace, _filename, _path, content_type):
        assert content_type == "text/csv; charset=utf-8"
        cancel_db = factory()
        try:
            row = cancel_db.query(AdhocRunExport).filter(
                AdhocRunExport.id == created["id"]
            ).one()
            assert row.status == "running"
            row.status = "cancel_requested"
            cancel_db.commit()
        finally:
            cancel_db.close()

    monkeypatch.setattr(export_service.artifact_s3, "put_shared_object_file", _upload)
    monkeypatch.setattr(
        export_service.artifact_s3,
        "delete_shared_object",
        lambda namespace, key: deleted.append((namespace, key)),
    )
    created = client.post(
        f"/api/adhoc-runs/{ids['run']}/exports",
        json={"format": "csv", "statement_index": 0},
    ).json()

    export_service.process_export(created["id"])

    db = factory()
    try:
        saved = db.query(AdhocRunExport).filter(
            AdhocRunExport.id == created["id"]
        ).one()
        assert saved.status == "cancelled"
        assert saved.storage_key is None
        assert deleted == [(export_service.EXPORT_NAMESPACE, saved.file_name)]
    finally:
        db.close()


def test_export_dispatcher_does_not_duplicate_lingering_thread(monkeypatch):
    class LingeringThread:
        def __init__(self):
            self.join_calls = 0

        def is_alive(self):
            return True

        def join(self, timeout=None):
            assert timeout == 5
            self.join_calls += 1

    lingering = LingeringThread()
    monkeypatch.setattr(export_service, "_thread", lingering)
    export_service.stop_export_dispatcher()
    assert lingering.join_calls == 1
    assert export_service._thread is lingering

    export_service.start_export_dispatcher()
    assert export_service._thread is lingering

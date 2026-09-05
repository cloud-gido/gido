# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""文件导入生产基线：checksum / finalize 锁 / cleanup / stale / staging publish / 幂等。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.file_import_store import (
    cleanup_orphan_uploads,
    finalize_chunked_upload,
    init_chunked_upload,
    save_upload_chunk,
)
from app.services.file_import_exec import _publish_staging_to_target
from app.services.sync_worker import reclaim_stale_running


@pytest.fixture()
def upload_root(tmp_path, monkeypatch):
    root = tmp_path / "imports"
    monkeypatch.setattr("app.core.config.settings.FILE_IMPORT_UPLOAD_DIR", str(root))
    monkeypatch.setattr("app.services.file_import_store.file_import_shared_enabled", lambda: False)
    return root


def test_chunk_checksum_mismatch(upload_root):
    raw = b"abcdefghij"
    init = init_chunked_upload(
        workspace_id=1,
        user_id=7,
        filename="a.csv",
        size_bytes=len(raw),
        total_chunks=1,
        chunk_bytes=len(raw),
    )
    fid = init["file_id"]
    with pytest.raises(ValueError, match="checksum"):
        save_upload_chunk(
            workspace_id=1,
            file_id=fid,
            chunk_index=0,
            content=raw,
            expected_sha256="0" * 64,
            user_id=7,
        )


def test_chunk_checksum_ok_and_content_sha_on_finalize(upload_root):
    parts = [b"hello-", b"world!"]
    blob = b"".join(parts)
    init = init_chunked_upload(
        workspace_id=2,
        user_id=1,
        filename="b.csv",
        size_bytes=len(blob),
        total_chunks=2,
        chunk_bytes=6,
    )
    fid = init["file_id"]
    for i, p in enumerate(parts):
        r = save_upload_chunk(
            workspace_id=2,
            file_id=fid,
            chunk_index=i,
            content=p,
            expected_sha256=hashlib.sha256(p).hexdigest(),
            user_id=1,
        )
        assert r["sha256"] == hashlib.sha256(p).hexdigest()
    meta = finalize_chunked_upload(workspace_id=2, file_id=fid, user_id=1)
    assert meta["status"] == "ready"
    assert meta["content_sha256"] == hashlib.sha256(blob).hexdigest()
    # 幂等二次 finalize
    meta2 = finalize_chunked_upload(workspace_id=2, file_id=fid, user_id=1)
    assert meta2["content_sha256"] == meta["content_sha256"]


def test_finalize_lock_busy(upload_root):
    raw = b"id,name\n1,a\n"
    init = init_chunked_upload(
        workspace_id=3,
        user_id=1,
        filename="c.csv",
        size_bytes=len(raw),
        total_chunks=1,
    )
    fid = init["file_id"]
    save_upload_chunk(workspace_id=3, file_id=fid, chunk_index=0, content=raw, user_id=1)

    with patch(
        "app.services.distributed_lock.acquire_distributed_lock",
        return_value=None,
    ):
        with pytest.raises(ValueError, match="正在合并"):
            finalize_chunked_upload(workspace_id=3, file_id=fid, user_id=1)


def test_upload_owner_enforced(upload_root):
    raw = b"x"
    init = init_chunked_upload(
        workspace_id=4,
        user_id=10,
        filename="d.csv",
        size_bytes=1,
        total_chunks=1,
    )
    fid = init["file_id"]
    with pytest.raises(ValueError, match="无权"):
        save_upload_chunk(
            workspace_id=4,
            file_id=fid,
            chunk_index=0,
            content=raw,
            user_id=99,
        )


def test_cleanup_orphan_uploads(upload_root, monkeypatch):
    import os

    from app.core.database import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models.workspace import FileImportVersion, SyncTask, User, Workspace
    from app.models import rbac_models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    u = User(username="u1", email="u1@t.com", hashed_password="x", is_admin=True, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    ws = Workspace(name="w", owner_id=u.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    task = SyncTask(
        workspace_id=ws.id,
        name="t",
        src_datasource_id=1,
        dst_datasource_id=1,
        src_table="f",
        dst_table="t1",
        sync_mode="file_import",
        sync_config={},
        created_by=u.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    kept_fid = "keptfile000000000000000000000001"
    orphan_fid = "orphan00000000000000000000000001"
    db.add(
        FileImportVersion(
            sync_task_id=task.id,
            workspace_id=ws.id,
            file_id=kept_fid,
            columns=[{"name": "id", "type": "bigint"}],
            operation_mode="create",
            quality_mode="strict",
            status="active",
        )
    )
    ws_id = int(ws.id)
    db.commit()
    db.close()

    for fid in (kept_fid, orphan_fid):
        folder = upload_root / str(ws_id) / fid
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(
            json.dumps({"file_id": fid, "workspace_id": ws_id, "status": "ready"}),
            encoding="utf-8",
        )

    orphan = upload_root / str(ws_id) / orphan_fid
    ts = (datetime.utcnow() - timedelta(days=10)).timestamp()
    os.utime(orphan / "meta.json", (ts, ts))

    monkeypatch.setattr("app.core.database.SessionLocal", Session)
    result = cleanup_orphan_uploads(older_than_hours=1)

    assert result["removed"] >= 1
    assert (upload_root / str(ws_id) / kept_fid).is_dir()
    assert not orphan.exists()


def test_reclaim_stale_running():
    from app.core.database import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models.workspace import SyncRecord, SyncTask, User, Workspace
    from app.models import rbac_models  # noqa: F401

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = Session()
    u = User(username="u2", email="u2@t.com", hashed_password="x", is_admin=True, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    ws = Workspace(name="w2", owner_id=u.id, timezone="Asia/Shanghai")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    task = SyncTask(
        workspace_id=ws.id,
        name="t2",
        src_datasource_id=1,
        dst_datasource_id=1,
        src_table="f",
        dst_table="t2",
        sync_mode="file_import",
        sync_config={},
        last_run_status="running",
        created_by=u.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    old = datetime.utcnow() - timedelta(hours=5)
    rec = SyncRecord(
        sync_task_id=task.id,
        status="running",
        started_at=old,
        heartbeat_at=old,
        execution_key="ek1",
        phase="loading",
    )
    db.add(rec)
    db.commit()

    n = reclaim_stale_running(db)
    assert n == 1
    db.refresh(rec)
    db.refresh(task)
    assert rec.status == "failed"
    assert rec.phase == "stale"
    assert task.last_run_status == "failed"
    db.close()


def test_publish_staging_mysql_create_and_append():
    class _Ds:
        ds_type = "mysql"
        database = "db"
        host = "h"
        port = 3306
        username = "u"
        password = "p"
        extra_config = {}

    cur = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    opened = ("mysql", conn)

    with patch("app.services.file_import_exec.open_connection") as oc:
        oc.return_value.__enter__ = MagicMock(return_value=opened)
        oc.return_value.__exit__ = MagicMock(return_value=False)
        with patch("app.services.file_import_exec.assert_supported_ds", return_value="mysql"):
            _publish_staging_to_target(
                _Ds(),
                lt="mysql",
                target="t_dst",
                staging="_fi_stg_abc",
                mode="create",
                target_ddl="CREATE TABLE t_dst(...)",
                database_override=None,
                target_existed=False,
            )
            assert any("RENAME TABLE" in str(c) for c in cur.execute.call_args_list)

    cur.reset_mock()
    with patch("app.services.file_import_exec.open_connection") as oc:
        oc.return_value.__enter__ = MagicMock(return_value=opened)
        oc.return_value.__exit__ = MagicMock(return_value=False)
        with patch("app.services.file_import_exec.assert_supported_ds", return_value="mysql"):
            _publish_staging_to_target(
                _Ds(),
                lt="mysql",
                target="t_dst",
                staging="_fi_stg_abc",
                mode="append",
                target_ddl="CREATE TABLE t_dst(...)",
                database_override=None,
                target_existed=True,
            )
            sqls = [str(c.args[0]) for c in cur.execute.call_args_list]
            assert any("INSERT INTO" in s for s in sqls)
            assert any("DROP TABLE" in s for s in sqls)


def test_publish_staging_doris_replace_fails_clearly():
    class _Ds:
        ds_type = "doris"
        database = "db"
        host = "h"
        port = 9030
        username = "u"
        password = "p"
        extra_config = {}

    cur = MagicMock()
    cur.execute.side_effect = Exception("REPLACE not supported")
    conn = MagicMock()
    conn.cursor.return_value = cur
    opened = ("mysql", conn)  # doris uses mysql protocol cursor path

    with patch("app.services.file_import_exec.open_connection") as oc:
        oc.return_value.__enter__ = MagicMock(return_value=opened)
        oc.return_value.__exit__ = MagicMock(return_value=False)
        with patch("app.services.file_import_exec.assert_supported_ds", return_value="doris"):
            with pytest.raises(ValueError, match="不支持原子 REPLACE"):
                _publish_staging_to_target(
                    _Ds(),
                    lt="doris",
                    target="t_dst",
                    staging="_fi_stg_abc",
                    mode="replace",
                    target_ddl="CREATE TABLE t_dst(...)",
                    database_override="db",
                    target_existed=True,
                )

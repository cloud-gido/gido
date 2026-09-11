# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""运行采集是 GIDO 自己的能力：后台常驻、健康度可见、执行引擎不外露。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-for-collector")

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.services import run_collector


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def isolated_health():
    """健康度默认落在进程内；测试里屏蔽 Redis，避免跨用例串味。"""
    run_collector._local_health.clear()
    with patch("app.services.shared_state.cache_get", return_value=None), \
         patch("app.services.shared_state.cache_set", return_value=None):
        yield
    run_collector._local_health.clear()


def test_collect_runs_reports_disabled_without_touching_engine(db):
    with patch("app.services.ds_runtime.get_dolphin_runtime", return_value=SimpleNamespace(enabled=False)):
        out = run_collector.collect_runs(db)
    assert out["collected"] is False
    assert out["reason"] == "scheduler_disabled"

    health = run_collector.collector_health(db)
    assert health["enabled"] is False
    # 未启用不算「采集落后」，否则实例中心会一直挂红
    assert health["stale"] is False


def test_collect_runs_records_success_health(db):
    stats = {
        "definitions_scanned": 3,
        "ingested": 2,
        "updated_from_ds": 1,
        "node_rows_touched": 5,
        "command_types_filled": 0,
        "skipped_unbound": 1,
    }
    with patch("app.services.ds_runtime.get_dolphin_runtime", return_value=SimpleNamespace(enabled=True)), \
         patch("app.services.ds_runtime.refresh_ds_client"), \
         patch("app.services.dolphin_instance_sync.sync_from_dolphin_definitions", return_value=stats), \
         patch("app.services.dolphin_instance_sync.patch_instances_from_ds_detail", return_value=(9, 4, 2)):
        out = run_collector.collect_runs(db)

    assert out["collected"] is True
    assert out["ingested"] == 2
    assert out["finalized_from_detail"] == 4
    assert out["command_types_filled"] == 2

    health = run_collector.collector_health(db)
    assert health["enabled"] is True
    assert health["stale"] is False
    assert health["lag_seconds"] is not None and health["lag_seconds"] < 5
    assert health["runs_ingested"] == 2
    assert health["unbound_workflows"] == 1
    assert health["last_error"] is None


def test_collect_runs_surfaces_failure_in_health(db):
    with patch("app.services.ds_runtime.get_dolphin_runtime", return_value=SimpleNamespace(enabled=True)), \
         patch("app.services.ds_runtime.refresh_ds_client"), \
         patch(
             "app.services.dolphin_instance_sync.sync_from_dolphin_definitions",
             side_effect=RuntimeError("token expired"),
         ):
        with pytest.raises(RuntimeError):
            run_collector.collect_runs(db)

    health = run_collector.collector_health(db)
    # 从未成功过 + 引擎已启用 = 落后，实例中心和告警中心都要红出来
    assert health["stale"] is True
    assert "token expired" in health["last_error"]


def test_single_workspace_collection_does_not_claim_global_health(db):
    stats = {
        "definitions_scanned": 1,
        "ingested": 0,
        "updated_from_ds": 0,
        "node_rows_touched": 0,
        "command_types_filled": 0,
        "skipped_unbound": 0,
    }
    with patch("app.services.ds_runtime.get_dolphin_runtime", return_value=SimpleNamespace(enabled=True)), \
         patch("app.services.ds_runtime.refresh_ds_client"), \
         patch("app.services.dolphin_instance_sync.sync_from_dolphin_definitions", return_value=stats), \
         patch("app.services.dolphin_instance_sync.patch_instances_from_ds_detail", return_value=(0, 0, 0)):
        run_collector.collect_runs(db, workspace_id=7)

    assert run_collector._read_health().get("last_success_at") is None

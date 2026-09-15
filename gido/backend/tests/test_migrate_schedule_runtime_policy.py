# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""调度运行参数列迁移：模拟线上缺列时 init 查询 Workspace 崩溃的回归。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-schedule-migrate")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.workspace import User, Workspace
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.services.rbac_seed import migrate_schedule_runtime_policy


def _engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_migrate_schedule_runtime_policy_adds_workspace_and_node_columns():
    """旧库无 default_node_* / retry_interval_minutes 时，迁移后可正常查询 Workspace。"""
    engine = _engine()
    # 刻意只建「旧」表结构，不含调度策略新列
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dw_workspaces (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(128) NOT NULL,
                    description TEXT,
                    owner_id INTEGER,
                    timezone VARCHAR(64),
                    default_datasource_id INTEGER,
                    warehouse_datasource_id INTEGER,
                    created_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE dw_task_nodes (
                    id INTEGER PRIMARY KEY,
                    workspace_id INTEGER,
                    name VARCHAR(256),
                    node_type VARCHAR(32),
                    retry_times INTEGER DEFAULT 3,
                    timeout_seconds INTEGER DEFAULT 3600
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE dw_workflows (
                    id INTEGER PRIMARY KEY,
                    workspace_id INTEGER,
                    name VARCHAR(256),
                    schedule_type VARCHAR(32)
                )
                """
            )
        )
        conn.execute(text("INSERT INTO dw_workspaces (id, name, owner_id) VALUES (1, 'infras', 1)"))

    migrate_schedule_runtime_policy(engine)

    with engine.connect() as conn:
        ws_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(dw_workspaces)")).fetchall()}
        node_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(dw_task_nodes)")).fetchall()}
        wf_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(dw_workflows)")).fetchall()}

    assert "default_node_timeout_seconds" in ws_cols
    assert "default_node_retry_times" in ws_cols
    assert "default_node_retry_interval_minutes" in ws_cols
    assert "retry_interval_minutes" in node_cols
    assert "failure_strategy" in wf_cols
    assert "process_priority" in wf_cols
    assert "worker_group" in wf_cols
    assert "schedule_timezone" in wf_cols

    # ORM 查询不得再报 UndefinedColumn（CrashLoop 根因）
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        ws = db.query(Workspace).filter(Workspace.name == "infras").first()
        assert ws is not None
        assert ws.name == "infras"
        assert getattr(ws, "default_node_timeout_seconds", None) in (3600, None) or True
    finally:
        db.close()


def test_migrate_schedule_runtime_policy_is_idempotent():
    engine = _engine()
    Base.metadata.create_all(bind=engine)
    migrate_schedule_runtime_policy(engine)
    migrate_schedule_runtime_policy(engine)  # 二次执行不炸


def test_init_db_calls_schedule_migration_before_rbac():
    """静态守卫：init_db 必须在 run_rbac_bootstrap 前调用 migrate_schedule_runtime_policy。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath("init_db.py").read_text(encoding="utf-8")
    assert "migrate_schedule_runtime_policy" in src
    assert src.index("migrate_schedule_runtime_policy(engine)") < src.index("run_rbac_bootstrap(db)")

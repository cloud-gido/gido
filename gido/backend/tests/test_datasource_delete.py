# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据源删除占用预检。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-ds-delete")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.workspace import DataSource, TaskNode, User, Workspace
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401
from app.services.datasource_delete import assert_datasource_deletable, collect_datasource_usages


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
            created_by=1,
        )
    )
    s.commit()
    yield s
    s.close()


def test_unused_datasource_deletable(db):
    ds = db.query(DataSource).filter(DataSource.id == 10).one()
    assert collect_datasource_usages(db, 10) == []
    assert_datasource_deletable(db, ds)


def test_datasource_in_use_blocked(db):
    db.add(
        TaskNode(
            workspace_id=1,
            name="n1",
            node_type="SQL",
            script_content="select 1",
            datasource_id=10,
            created_by=1,
        )
    )
    db.commit()
    ds = db.query(DataSource).filter(DataSource.id == 10).one()
    with pytest.raises(HTTPException) as ei:
        assert_datasource_deletable(db, ds)
    assert ei.value.status_code == 409
    assert "数据开发节点" in str(ei.value.detail)

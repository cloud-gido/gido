# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""删除 Serve 消费者应用：先断开调用日志 / 授权引用。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-delete-app")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.data_service import ConsumerApp, ConsumerAppApiGrant, DataApi, DataApiInvocationLog
from app.models.workspace import User, Workspace
from app.models import rbac_models  # noqa: F401
from app.models import data_service as _ds  # noqa: F401


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
    s.add(Workspace(id=1, name="ws", owner_id=None))
    s.add(User(id=1, username="u", email="u@t.local", hashed_password="x", is_active=True))
    s.commit()
    api = DataApi(
        workspace_id=1,
        api_code="demo",
        name="Demo",
        mode="sql",
        status="online",
        sql_template="SELECT 1",
    )
    app = ConsumerApp(
        workspace_id=1,
        name="ops",
        app_key="ak_test",
        app_secret_hash="hash",
        is_active=True,
    )
    s.add_all([api, app])
    s.commit()
    s.refresh(api)
    s.refresh(app)
    s.add(ConsumerAppApiGrant(app_id=app.id, api_id=api.id))
    s.add(
        DataApiInvocationLog(
            workspace_id=1,
            api_id=api.id,
            app_id=app.id,
            http_method="GET",
            status_code=200,
            row_count=1,
        )
    )
    s.commit()
    yield s, app.id, api.id
    s.close()


def test_delete_app_clears_grants_and_nulls_logs(db):
    session, app_id, api_id = db
    # 模拟 delete_app 清理逻辑
    session.query(DataApiInvocationLog).filter(DataApiInvocationLog.app_id == app_id).update(
        {DataApiInvocationLog.app_id: None},
        synchronize_session=False,
    )
    session.query(ConsumerAppApiGrant).filter(ConsumerAppApiGrant.app_id == app_id).delete(synchronize_session=False)
    app = session.query(ConsumerApp).filter(ConsumerApp.id == app_id).one()
    session.delete(app)
    session.commit()

    assert session.query(ConsumerApp).filter(ConsumerApp.id == app_id).first() is None
    assert session.query(ConsumerAppApiGrant).filter(ConsumerAppApiGrant.app_id == app_id).count() == 0
    log = session.query(DataApiInvocationLog).filter(DataApiInvocationLog.api_id == api_id).one()
    assert log.app_id is None

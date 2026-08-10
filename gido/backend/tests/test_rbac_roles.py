# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""平台角色权限矩阵（对齐商业化 Dev / Ops / Analyst 分离）。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-rbac-roles")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import perm_codes as P
from app.core.access import get_user_permission_codes
from app.core.database import Base
from app.core.security import get_password_hash
from app.models.workspace import User
from app.models import rbac_models  # noqa: F401
from app.services.rbac_seed import run_rbac_bootstrap


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    run_rbac_bootstrap(db)
    return db


def _user_with_role(db, code: str) -> User:
    from app.models.rbac_models import Role

    role = db.query(Role).filter(Role.code == code).one()
    u = User(
        username=f"u_{code}",
        email=f"{code}@t.local",
        hashed_password=get_password_hash("x"),
        is_admin=False,
        is_active=True,
        role_id=role.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_operator_has_run_not_write_across_products():
    db = _session()
    try:
        codes = get_user_permission_codes(db, _user_with_role(db, "operator"))
        assert P.GIDO_BATCH_STUDIO_READ in codes
        assert P.GIDO_BATCH_STUDIO_RUN in codes
        assert P.GIDO_BATCH_STUDIO_WRITE not in codes
        assert P.GIDO_STREAM_READ in codes
        assert P.GIDO_STREAM_RUN in codes
        assert P.GIDO_STREAM_WRITE not in codes
        assert P.GIDO_SERVICE_READ in codes
        assert P.GIDO_SERVICE_RUN in codes
        assert P.GIDO_SERVICE_WRITE not in codes
        assert P.GIDO_BATCH_WORKFLOW_RUN in codes
        assert P.GIDO_BATCH_WORKFLOW_WRITE not in codes
        assert not any(c.startswith("system:") for c in codes)
    finally:
        db.close()


def test_analyst_is_probe_datamap_datasource_only():
    db = _session()
    try:
        codes = get_user_permission_codes(db, _user_with_role(db, "analyst"))
        assert codes == {
            P.WORKSPACE_READ,
            P.GIDO_BATCH_PROBE_READ,
            P.GIDO_BATCH_DATAMAP_READ,
            P.GIDO_BATCH_DATASOURCE_READ,
        }
    finally:
        db.close()


def test_developer_has_business_write_without_system():
    db = _session()
    try:
        codes = get_user_permission_codes(db, _user_with_role(db, "developer"))
        assert P.GIDO_BATCH_STUDIO_WRITE in codes
        assert P.GIDO_STREAM_WRITE in codes
        assert P.GIDO_SERVICE_WRITE in codes
        assert P.SYSTEM_USER_WRITE not in codes
        assert P.SYSTEM_INTEGRATION_WRITE not in codes
    finally:
        db.close()


def test_frontend_perm_codes_cover_backend_all():
    """防止再出现 Studio WRITE 常量缺失导致 UI 永久只读。"""
    import re
    from pathlib import Path

    perm_ts = Path(__file__).resolve().parents[2] / "frontend" / "src" / "perm.ts"
    text = perm_ts.read_text(encoding="utf-8")
    for code in P.ALL_PERMISSIONS:
        assert f"'{code}'" in text or f'"{code}"' in text, f"frontend P missing {code}"
    # 关键键存在
    assert re.search(r"GIDO_BATCH_STUDIO_WRITE\s*:\s*'gido:batch:studio:write'", text)
    assert re.search(r"GIDO_BATCH_STUDIO_RUN\s*:\s*'gido:batch:studio:run'", text)

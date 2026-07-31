# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Helpers for optional HA integration tests (real Redis / PostgreSQL)."""
from __future__ import annotations

import os
from typing import Optional


def redis_test_url() -> Optional[str]:
    for key in ("GIDO_TEST_REDIS_URL", "REDIS_URL"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return "redis://127.0.0.1:6379/15"


def redis_reachable(url: Optional[str] = None) -> bool:
    target = (url or redis_test_url() or "").strip()
    if not target:
        return False
    try:
        import redis
        from app.services.shared_state import parse_redis_endpoint

        ep = parse_redis_endpoint(target, (os.environ.get("REDIS_PASSWORD") or "").strip())
        kwargs = {
            "host": ep.host,
            "port": ep.port,
            "db": ep.database,
            "socket_connect_timeout": 1,
            "socket_timeout": 1,
            "decode_responses": True,
        }
        if ep.password:
            kwargs["password"] = ep.password
        if ep.username:
            kwargs["username"] = ep.username
        if ep.ssl:
            kwargs["ssl"] = True
            kwargs["ssl_cert_reqs"] = None
        client = redis.Redis(**kwargs)
        client.ping()
        return True
    except Exception:
        return False


def pg_test_url() -> Optional[str]:
    for key in ("GIDO_TEST_DATABASE_URL", "DATABASE_URL"):
        value = (os.environ.get(key) or "").strip()
        if value.startswith("postgresql"):
            return value
    return None


def pg_reachable(url: Optional[str] = None) -> bool:
    target = (url or pg_test_url() or "").strip()
    if not target.startswith("postgresql"):
        return False
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(target, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False

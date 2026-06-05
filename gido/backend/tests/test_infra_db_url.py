# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""INFRA_GIDO_DB_* 组装元数据库连接串。"""
import pytest

from app.core.infra_db_url import (
    build_postgres_sqlalchemy_url,
    infra_db_env_any_set,
    infra_db_env_complete,
    parse_pg_service_url,
)


def test_parse_pg_service_url_host_port():
    assert parse_pg_service_url("db.internal:5432") == ("db.internal", 5432, None)


def test_parse_pg_service_url_host_only():
    assert parse_pg_service_url("localhost") == ("localhost", 5432, None)


def test_parse_pg_service_url_host_port_db():
    assert parse_pg_service_url("db.internal:5432/gido") == ("db.internal", 5432, "gido")


def test_parse_pg_service_url_postgresql_scheme():
    h, p, d = parse_pg_service_url("postgresql://db.internal:5432/gido")
    assert h == "db.internal" and p == 5432 and d == "gido"


def test_build_postgres_sqlalchemy_url_encodes_password():
    u = build_postgres_sqlalchemy_url(
        service_url="127.0.0.1:5432",
        user="root",
        password="a!b",
        database_name="gido",
    )
    assert u.startswith("postgresql+psycopg2://")
    assert "127.0.0.1:5432/gido" in u
    assert "%21" in u or "a%21b" in u


def test_infra_complete_requires_password_key_present():
    assert infra_db_env_complete(
        service_url="h:5432",
        service_user="u",
        service_password=None,
        db_url="d",
    ) is False
    assert infra_db_env_complete(
        service_url="h:5432",
        service_user="u",
        service_password="",
        db_url="d",
    ) is True


def test_infra_any_set_detects_reader_only():
    assert infra_db_env_any_set(
        service_url=None,
        service_user=None,
        service_password=None,
        db_url=None,
        service_reader="ro_user",
    ) is True


def test_settings_resolved_prefers_infra(monkeypatch):
    monkeypatch.delenv("INFRA_GIDO_DB_SERVICE_URL", raising=False)
    monkeypatch.delenv("INFRA_GIDO_DB_SERVICE_USER", raising=False)
    monkeypatch.delenv("INFRA_GIDO_DB_SERVICE_PASSWORD", raising=False)
    monkeypatch.delenv("INFRA_GIDO_DB_URL", raising=False)
    monkeypatch.delenv("INFRA_GIDO_DB_SERVICE_READER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://x:y@legacy:5432/legacydb")
    from app.core.config import Settings

    s = Settings()
    assert s.resolved_database_url == "postgresql+psycopg2://x:y@legacy:5432/legacydb"


def test_settings_resolved_from_infra(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://ignored:ignored@ignored:5432/ignored")
    monkeypatch.setenv("INFRA_GIDO_DB_SERVICE_URL", "pg.example.com:5432")
    monkeypatch.setenv("INFRA_GIDO_DB_SERVICE_USER", "dw_writer")
    monkeypatch.setenv("INFRA_GIDO_DB_SERVICE_PASSWORD", "s3cret!")
    monkeypatch.setenv("INFRA_GIDO_DB_URL", "gido")
    from app.core.config import Settings

    s = Settings()
    out = s.resolved_database_url
    assert "pg.example.com:5432/gido" in out
    assert "dw_writer" in out
    assert "ignored" not in out


def test_settings_partial_infra_raises(monkeypatch):
    monkeypatch.delenv("INFRA_GIDO_DB_SERVICE_USER", raising=False)
    monkeypatch.delenv("INFRA_GIDO_DB_SERVICE_PASSWORD", raising=False)
    monkeypatch.delenv("INFRA_GIDO_DB_URL", raising=False)
    monkeypatch.setenv("INFRA_GIDO_DB_SERVICE_URL", "h:5432")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://a:b@c:5432/d")
    from app.core.config import Settings

    with pytest.raises(ValueError, match="INFRA_GIDO"):
        Settings().resolved_database_url

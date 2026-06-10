# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.url import make_url
from app.core.config import settings
import os


def _resolve_db_url(url: str) -> str:
    if url.startswith("sqlite:///./"):
        abs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", url[len("sqlite:///./"):])
        return "sqlite:///" + os.path.abspath(abs_path)
    return url


def _ensure_postgresql_database(url: str) -> None:
    """若使用 PostgreSQL 且库不存在，则创建（须能连上 maintenance db `postgres` 且具备 CREATEDB 或等价权限）。"""
    if not url.startswith("postgresql"):
        return
    parsed = make_url(url)
    if not str(parsed.drivername).startswith("postgresql"):
        return
    dbname = parsed.database
    if not dbname:
        return
    import psycopg2
    from psycopg2 import sql as psql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    base_kw = {
        "host": parsed.host or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
    }

    def _fail_hint(raw: str) -> RuntimeError:
        hint = (
            "PostgreSQL：无法使用当前元数据库连接配置中的账号连接数据库。"
            "请核对 INFRA_GIDO_DB_SERVICE_* / INFRA_GIDO_DB_URL 或 DATABASE_URL 中的用户名/密码；"
            "并确认 `host:port` 指向的就是你创建库的那台服务（本机 5432 上若还有别的 PG，易出现「密码明明对却认证失败」）。"
        )
        if "password authentication failed" in raw.lower():
            hint += " 当前错误为「密码认证失败」：多为 .env 中密码与容器 `POSTGRES_PASSWORD` 不一致，或连错了实例。"
        return RuntimeError(f"{hint}\n原始错误：{raw}")

    # 目标库已存在且账号可用时，直接返回（不必再连 maintenance 库 `postgres`）
    try:
        c0 = psycopg2.connect(**base_kw, dbname=dbname)
        c0.close()
        return
    except Exception as e0:
        raw0 = str(e0).strip()
        low = raw0.lower()
        # 仅当明确是「数据库不存在」时才尝试 CREATE DATABASE；其余（含密码错误）立即报错
        if not ("does not exist" in low and "database" in low):
            raise _fail_hint(raw0) from e0

    try:
        conn = psycopg2.connect(**base_kw, dbname="postgres")
    except Exception as e:
        raw = str(e).strip()
        raise _fail_hint(raw) from e
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone():
                return
            cur.execute(
                psql.SQL("CREATE DATABASE {} ENCODING {}").format(
                    psql.Identifier(dbname),
                    psql.Literal("UTF8"),
                )
            )
    finally:
        conn.close()


def _ensure_mysql_database(url: str) -> None:
    """若使用 MySQL 且库不存在，则创建（可选回退；默认元数据为 PostgreSQL）。"""
    if not url.startswith("mysql"):
        return
    parsed = make_url(url)
    if not str(parsed.drivername).startswith("mysql"):
        return
    dbname = parsed.database
    if not dbname:
        return
    import pymysql

    connect_kwargs = {
        "host": parsed.host or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "root",
        "password": parsed.password or "",
        "charset": "utf8mb4",
    }
    conn = pymysql.connect(**connect_kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{dbname}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
    finally:
        conn.close()


_db_url = _resolve_db_url(settings.resolved_database_url)
_ensure_postgresql_database(_db_url)
_ensure_mysql_database(_db_url)

_engine_kwargs = {}
if "sqlite" in _db_url:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_pre_ping"] = True
    if "mysql" in _db_url or "postgresql" in _db_url:
        _engine_kwargs["pool_recycle"] = 3600

engine = create_engine(_db_url, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

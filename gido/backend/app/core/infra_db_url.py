# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""将运维拆分的环境变量组装为 SQLAlchemy 使用的 PostgreSQL 连接串（不含凭据拼接在单一「业务 URL」中）。"""
from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import quote_plus, urlparse


def parse_pg_service_url(service_url: str) -> Tuple[str, int, Optional[str]]:
    """
    解析 INFRA_GIDO_DB_SERVICE_URL（不含用户名密码）。
    支持：host | host:port | host:port/dbname | postgresql://host:port/dbname | postgresql://host/db
    """
    s = (service_url or "").strip()
    if not s:
        raise ValueError("INFRA_GIDO_DB_SERVICE_URL 为空")

    if "://" in s:
        u = urlparse(s if "://" in s else f"postgresql://{s}")
        host = (u.hostname or "").strip() or "127.0.0.1"
        port = int(u.port or 5432)
        db = (u.path or "").strip("/") or None
        return host, port, db

    # host:port/dbname
    if "/" in s and not s.startswith("["):
        left, right = s.split("/", 1)
        left = left.strip()
        right = right.strip()
        if ":" in left:
            host, port_s = left.rsplit(":", 1)
            return host.strip(), int(port_s.strip()), right or None
        return left, 5432, right or None

    # host:port 或 host
    if ":" in s:
        host, port_s = s.rsplit(":", 1)
        if port_s.isdigit():
            return host.strip(), int(port_s.strip()), None
    return s, 5432, None


def build_postgres_sqlalchemy_url(
    *,
    service_url: str,
    user: str,
    password: Optional[str],
    database_name: str,
    driver: str = "postgresql+psycopg2",
) -> str:
    """password 允许空串（如 trust / 无密码场景）。"""
    host, port, db_in_url = parse_pg_service_url(service_url)
    db = (db_in_url or "").strip() or (database_name or "").strip()
    db = db.strip("/")
    if not db:
        raise ValueError("数据库名缺失：请在 INFRA_GIDO_DB_URL 中配置库名，或在 INFRA_GIDO_DB_SERVICE_URL 路径中带上库名")
    pw = "" if password is None else str(password)
    user_q = quote_plus(user)
    pw_q = quote_plus(pw)
    return f"{driver}://{user_q}:{pw_q}@{host}:{port}/{db}"


def infra_db_env_any_set(
    *,
    service_url: Optional[str],
    service_user: Optional[str],
    service_password: Optional[str],
    db_url: Optional[str],
    service_reader: Optional[str],
) -> bool:
    return any(
        [
            (service_url or "").strip(),
            (service_user or "").strip(),
            service_password is not None,
            (db_url or "").strip(),
            (service_reader or "").strip(),
        ]
    )


def infra_db_env_complete(
    *,
    service_url: Optional[str],
    service_user: Optional[str],
    service_password: Optional[str],
    db_url: Optional[str],
) -> bool:
    """READER 不参与必填判断。"""
    return bool(
        (service_url or "").strip()
        and (service_user or "").strip()
        and service_password is not None
        and (db_url or "").strip()
    )


def infra_db_env_partial_error_message() -> str:
    return (
        "检测到已配置部分 INFRA_GIDO_DB_* 变量。生产环境请同时设置以下项（密码可为空串）：\n"
        "  INFRA_GIDO_DB_SERVICE_URL   — PostgreSQL 地址，如 host:5432 或 postgresql://host:5432/dbname（无账号密码）\n"
        "  INFRA_GIDO_DB_SERVICE_USER  — 读写账号\n"
        "  INFRA_GIDO_DB_SERVICE_PASSWORD — 读写密码\n"
        "  INFRA_GIDO_DB_URL           — 数据库名，如 gido（若 SERVICE_URL 已含路径库名可与之相同）\n"
        "可选：INFRA_GIDO_DB_SERVICE_READER — 只读账号用户名（预留给只读副本/报表，当前进程仍用读写账号连库）\n"
        "若暂不采用拆分变量，请去掉上述 INFRA_* 配置，仅使用 DATABASE_URL（或 GIDO_DATABASE_URL）。"
    )

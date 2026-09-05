# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据集成：数据源连接、元数据探测（MySQL / Doris / PostgreSQL）。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from app.models.workspace import DataSource
from app.services.datasource_mysql_user import mysql_protocol_connect_user

SUPPORTED_DS_TYPES = frozenset({"mysql", "doris", "postgresql"})


def normalize_ds_type(ds: DataSource) -> str:
    return (ds.ds_type or "").strip().lower()


def assert_supported_ds(ds: DataSource, role: str = "数据源") -> str:
    lt = normalize_ds_type(ds)
    if lt not in SUPPORTED_DS_TYPES:
        raise ValueError(f"{role} 类型 {ds.ds_type!r} 暂不支持数据集成，仅支持 mysql / doris / postgresql")
    return lt


@contextmanager
def open_connection(ds: DataSource, *, database: Optional[str] = None):
    lt = assert_supported_ds(ds)
    if lt in ("mysql", "doris"):
        import pymysql

        conn = pymysql.connect(
            host=ds.host,
            port=ds.port or 3306,
            user=mysql_protocol_connect_user(ds),
            password=ds.password or "",
            database=database if database is not None else (ds.database or ""),
            connect_timeout=15,
            charset="utf8mb4",
        )
        try:
            yield ("mysql", conn)
        finally:
            conn.close()
        return

    import psycopg2

    dbname = (database if database is not None else (ds.database or "")).strip()
    if not dbname:
        raise ValueError("PostgreSQL 数据源未配置数据库名")
    ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
    pg_schema = str(ex.get("schema") or "public").strip() or "public"
    conn = psycopg2.connect(
        host=ds.host or "127.0.0.1",
        port=ds.port or 5432,
        user=(ds.username or "").strip() or None,
        password=ds.password or "",
        dbname=dbname,
        connect_timeout=15,
    )
    try:
        yield ("postgresql", conn, pg_schema)
    finally:
        conn.close()


def _resolve_mysql_catalog(ds: DataSource, catalog: Optional[str] = None) -> str:
    schema = (catalog or ds.database or "").strip()
    if not schema:
        raise ValueError("未指定库名（catalog），且数据源未配置默认 database")
    return schema


def _resolve_pg_schema(ds: DataSource, catalog: Optional[str] = None) -> str:
    if catalog and catalog.strip():
        return catalog.strip()
    return pg_schema_for(ds)


def list_schemas(ds: DataSource, keyword: str = "") -> List[Dict[str, Any]]:
    """列出可补全的库/schema（MySQL/Doris=database，PostgreSQL=schema）。"""
    kw = (keyword or "").strip().lower()
    lt = assert_supported_ds(ds)
    rows: List[Dict[str, Any]] = []
    with open_connection(ds) as opened:
        if opened[0] == "mysql":
            _, conn = opened
            cur = conn.cursor()
            cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys') "
                "ORDER BY SCHEMA_NAME"
            )
            default = (ds.database or "").strip()
            for (name,) in cur.fetchall():
                if kw and kw not in str(name).lower():
                    continue
                rows.append({"name": name, "is_default": bool(default) and name == default})
            return rows

        _, conn, pg_schema = opened
        cur = conn.cursor()
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast') "
            "ORDER BY schema_name"
        )
        for (name,) in cur.fetchall():
            if kw and kw not in str(name).lower():
                continue
            rows.append({"name": name, "is_default": name == pg_schema})
        return rows


def list_tables(
    ds: DataSource,
    keyword: str = "",
    catalog: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出物理表。catalog=MySQL/Doris 库名，或 PostgreSQL schema；默认用数据源配置。"""
    kw = (keyword or "").strip().lower()
    lt = assert_supported_ds(ds)
    rows: List[Dict[str, Any]] = []
    with open_connection(ds) as opened:
        if opened[0] == "mysql":
            _, conn = opened
            cur = conn.cursor()
            schema = _resolve_mysql_catalog(ds, catalog)
            cur.execute(
                "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                (schema,),
            )
            for name, ttype, comment in cur.fetchall():
                if kw and kw not in str(name).lower() and kw not in str(comment or "").lower():
                    continue
                rows.append({
                    "name": name,
                    "type": ttype,
                    "comment": comment or "",
                    "catalog": schema,
                })
            return rows

        _, conn, _pg_default = opened
        cur = conn.cursor()
        schema = _resolve_pg_schema(ds, catalog)
        cur.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name",
            (schema,),
        )
        for name, ttype in cur.fetchall():
            if kw and kw not in str(name).lower():
                continue
            rows.append({"name": name, "type": ttype, "comment": "", "catalog": schema})
        return rows


def list_columns(
    ds: DataSource,
    table_name: str,
    catalog: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出列。支持 catalog.table；MySQL/Doris 也可用 catalog 参数指定库。"""
    table_name = (table_name or "").strip()
    if not table_name:
        raise ValueError("表名不能为空")
    # 允许 table_name 写成 db.table
    if "." in table_name and not catalog:
        parts = table_name.split(".", 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            catalog, table_name = parts[0].strip(), parts[1].strip()
    lt = assert_supported_ds(ds)
    with open_connection(ds) as opened:
        if opened[0] == "mysql":
            _, conn = opened
            cur = conn.cursor()
            schema = _resolve_mysql_catalog(ds, catalog)
            cur.execute(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                "ORDER BY ORDINAL_POSITION",
                (schema, table_name),
            )
            return [
                {
                    "name": row[0],
                    "type": row[1],
                    "nullable": str(row[2]).upper() == "YES",
                    "key": row[3] or "",
                    "catalog": schema,
                    "table": table_name,
                }
                for row in cur.fetchall()
            ]

        _, conn, _pg_default = opened
        cur = conn.cursor()
        schema = _resolve_pg_schema(ds, catalog)
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable,
                   (SELECT COUNT(*) > 0 FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = c.table_schema
                      AND kcu.table_name = c.table_name
                      AND kcu.column_name = c.column_name) AS is_pk
            FROM information_schema.columns c
            WHERE c.table_schema = %s AND c.table_name = %s
            ORDER BY c.ordinal_position
            """,
            (schema, table_name),
        )
        return [
            {
                "name": row[0],
                "type": row[1],
                "nullable": row[2] == "YES",
                "key": "PRI" if row[3] else "",
                "catalog": schema,
                "table": table_name,
            }
            for row in cur.fetchall()
        ]


def test_connection(ds: DataSource) -> Tuple[bool, str]:
    try:
        with open_connection(ds) as opened:
            if opened[0] == "mysql":
                _, conn = opened
                conn.cursor().execute("SELECT 1")
            else:
                _, conn, _ = opened
                conn.cursor().execute("SELECT 1")
        return True, "连接成功"
    except Exception as e:
        return False, str(e)


def quote_ident(lt: str, name: str) -> str:
    if lt == "postgresql":
        return f'"{name.replace(chr(34), chr(34) * 2)}"'
    return f"`{name.replace('`', '``')}`"


def pg_schema_for(ds: DataSource) -> str:
    ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
    return str(ex.get("schema") or "public").strip() or "public"

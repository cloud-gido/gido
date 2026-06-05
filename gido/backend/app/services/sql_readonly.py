# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""只读 SQL 拆分、校验与结果集列类型元数据。"""
from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, List, Optional, Sequence, Tuple
from uuid import UUID

from fastapi import HTTPException


def _strip_sql_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def split_sql_statements(sql: str, *, max_parts: int = 32) -> List[str]:
    """按分号拆分多条语句（忽略引号与注释内的分号）。"""
    raw = (sql or "").strip()
    if not raw:
        return []
    parts: List[str] = []
    buf: List[str] = []
    i = 0
    n = len(raw)
    in_sq = False
    in_dq = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = raw[i]
        nxt = raw[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
            i += 1
            continue

        if not in_sq and not in_dq:
            if ch == "-" and nxt == "-":
                in_line_comment = True
                buf.append(ch)
                buf.append(nxt)
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_block_comment = True
                buf.append(ch)
                buf.append(nxt)
                i += 2
                continue

        if ch == "'" and not in_dq:
            in_sq = not in_sq
            buf.append(ch)
            i += 1
            continue
        if ch == '"' and not in_sq:
            in_dq = not in_dq
            buf.append(ch)
            i += 1
            continue

        if ch == ";" and not in_sq and not in_dq:
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
                if len(parts) >= max_parts:
                    raise HTTPException(status_code=400, detail=f"最多支持 {max_parts} 条语句")
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def assert_readonly_statement(sql: str) -> str:
    core = (sql or "").strip().rstrip(";").strip()
    if not core:
        raise HTTPException(status_code=400, detail="存在空语句，请删除多余分号")
    cleaned = _strip_sql_comments(core).strip()
    if not re.match(r"^(WITH|SELECT)\b", cleaned, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="仅允许 SELECT 或 WITH…SELECT 只读查询")
    forbidden = (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "MERGE ",
        "DROP ",
        "ALTER ",
        "CREATE ",
        "TRUNCATE ",
        "GRANT ",
        "REVOKE ",
        "CALL ",
        "EXECUTE ",
        "EXEC ",
        "REPLACE ",
    )
    up = f" {cleaned.upper()} "
    for kw in forbidden:
        if kw in up:
            raise HTTPException(status_code=400, detail=f"禁止包含写操作或 DDL 关键字: {kw.strip()}")
    if re.search(r"\bINTO\b", cleaned, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="禁止使用 INTO（如 SELECT INTO / LOAD）")
    return core


def json_cell_value(v: Any) -> Any:
    """将数据库单元格转为可 JSON 序列化的值。"""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date, time)):
        return v.isoformat(sep=" ", timespec="seconds") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        try:
            return bytes(v).decode("utf-8", errors="replace")
        except Exception:
            return str(v)
    if isinstance(v, (list, dict)):
        return v
    return str(v)


_PG_ADAPTER_TYPE_NAMES = {
    "LONGINTEGER": "bigint",
    "INTEGER": "int",
    "STRING": "varchar",
    "UNICODE": "varchar",
    "DATETIME": "timestamp",
    "DATETIMETZ": "timestamptz",
    "FLOAT": "float",
    "DECIMAL": "decimal",
    "BOOLEAN": "bool",
    "DATE": "date",
    "TIME": "time",
    "BYTES": "bytea",
}


def _pg_type_label(oid: int) -> str:
    import psycopg2.extensions as ext

    t = ext.string_types.get(oid)
    if t is None:
        return f"oid_{oid}"
    if isinstance(t, type):
        name = t.__name__
        if name == "datetime":
            return "timestamp"
        return name.lower()
    # psycopg2._psycopg.type：用 .name，勿 str()（会带 psycopg2 前缀导致前端无法识别）
    adapter_name = getattr(t, "name", None)
    if adapter_name:
        key = str(adapter_name).upper()
        return _PG_ADAPTER_TYPE_NAMES.get(key, key.lower())
    return f"oid_{oid}"


def parse_readonly_statements(sql: str) -> List[str]:
    parts = split_sql_statements(sql)
    if not parts:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    return [assert_readonly_statement(p) for p in parts]


def column_types_from_description(ds_type: str, description: Optional[Sequence]) -> List[str]:
    """从 DB-API cursor.description 提取列类型展示名。"""
    if not description:
        return []
    lt = (ds_type or "").lower()
    out: List[str] = []
    if lt in ("mysql", "doris"):
        try:
            from pymysql.constants import FIELD_TYPE

            names = {
                FIELD_TYPE.DECIMAL: "decimal",
                FIELD_TYPE.TINY: "tinyint",
                FIELD_TYPE.SHORT: "smallint",
                FIELD_TYPE.LONG: "int",
                FIELD_TYPE.FLOAT: "float",
                FIELD_TYPE.DOUBLE: "double",
                FIELD_TYPE.NULL: "null",
                FIELD_TYPE.TIMESTAMP: "timestamp",
                FIELD_TYPE.LONGLONG: "bigint",
                FIELD_TYPE.INT24: "mediumint",
                FIELD_TYPE.DATE: "date",
                FIELD_TYPE.TIME: "time",
                FIELD_TYPE.DATETIME: "datetime",
                FIELD_TYPE.YEAR: "year",
                FIELD_TYPE.NEWDATE: "date",
                FIELD_TYPE.VARCHAR: "varchar",
                FIELD_TYPE.BIT: "bit",
                FIELD_TYPE.JSON: "json",
                FIELD_TYPE.NEWDECIMAL: "decimal",
                FIELD_TYPE.ENUM: "enum",
                FIELD_TYPE.SET: "set",
                FIELD_TYPE.BLOB: "blob",
                FIELD_TYPE.STRING: "string",
                FIELD_TYPE.CHAR: "char",
            }
            for col in description:
                code = col[1] if len(col) > 1 else None
                out.append(names.get(code, f"type_{code}"))
        except Exception:
            for col in description:
                out.append(str(col[1]) if len(col) > 1 else "unknown")
        return out

    if lt == "postgresql":
        try:
            for col in description:
                oid = col[1] if len(col) > 1 else None
                out.append(_pg_type_label(int(oid)) if oid is not None else "unknown")
        except Exception:
            for col in description:
                out.append(str(col[1]) if len(col) > 1 else "unknown")
        return out

    for col in description:
        out.append(str(col[1]) if len(col) > 1 else "unknown")
    return out


def result_set_from_cursor(ds_type: str, description: Optional[Sequence], rows: List, limit: int) -> dict:
    cols = [d[0] for d in description] if description else []
    types = column_types_from_description(ds_type, description)
    capped = rows[:limit]
    return {
        "columns": cols,
        "column_types": types,
        "rows": [[json_cell_value(v) for v in row] for row in capped],
        "total": len(rows),
        "truncated": len(rows) >= limit,
    }

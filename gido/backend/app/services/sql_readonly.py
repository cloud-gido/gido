# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""只读 SQL 拆分、校验与结果集列类型元数据。"""
from __future__ import annotations

import base64
import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from fastapi import HTTPException


def _strip_sql_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    # 兼容 MySQL 风格行注释
    s = re.sub(r"#[^\n]*", " ", s)
    # 兼容脚本/IDE 常见的双斜杠注释
    s = re.sub(r"//[^\n]*", " ", s)
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
            if ch == "#":
                in_line_comment = True
                buf.append(ch)
                i += 1
                continue
            if ch == "/" and nxt == "/":
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


# 浏览器 JSON.parse 只能安全表示到 2^53-1；雪花 BIGINT 超出后必须改成字符串。
_JS_MAX_SAFE_INTEGER = 2**53 - 1


def json_cell_value(v: Any) -> Any:
    """将数据库单元格转为可 JSON 序列化的值。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        if v > _JS_MAX_SAFE_INTEGER or v < -_JS_MAX_SAFE_INTEGER:
            return str(v)
        return v
    if isinstance(v, (str, float)):
        return v
    if isinstance(v, (datetime, date, time)):
        return v.isoformat(sep=" ", timespec="seconds") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "base64:" + base64.b64encode(bytes(v)).decode("ascii")
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
    # 允许用户用「注释包掉一整条 SQL」但仍留下分号等符号；
    # 清理注释后如果语句为空，则跳过而不是报仅允许 SELECT/WITH。
    valid_parts: List[str] = []
    for p in parts:
        cleaned = _strip_sql_comments(p).strip()
        if cleaned:
            valid_parts.append(p)
    if not valid_parts:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    return [assert_readonly_statement(p) for p in valid_parts]


def apply_readonly_row_limit(
    stmt: str,
    lim: int,
    *,
    overflow_probe: bool = False,
) -> str:
    """在只读语句末尾追加 LIMIT，避免子查询包装导致 ORDER BY 被优化器丢弃。"""
    core = (stmt or "").strip().rstrip(";").strip()
    if not core:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    cleaned = _strip_sql_comments(core)
    if re.search(r"\bLIMIT\s+\d", cleaned, re.IGNORECASE):
        return core
    cap = min(max(int(lim), 1), 10000)
    return f"{core} LIMIT {cap + 1 if overflow_probe else cap}"


# MySQL 二进制字符集。BLOB/TEXT、VARCHAR/VARBINARY、CHAR/BINARY 共用类型码，只靠它区分。
_MYSQL_BINARY_CHARSET = 63
_MYSQL_TYPE_BLOB = 252
_MYSQL_TYPE_VAR_STRING = 253
_MYSQL_TYPE_STRING = 254
_MYSQL_TYPE_NAMES = {
    0: "decimal",
    1: "tinyint",
    2: "smallint",
    3: "int",
    4: "float",
    5: "double",
    6: "null",
    7: "timestamp",
    8: "bigint",
    9: "mediumint",
    10: "date",
    11: "time",
    12: "datetime",
    13: "year",
    14: "date",
    15: "varchar",
    16: "bit",
    245: "json",
    246: "decimal",
    247: "enum",
    248: "set",
    255: "geometry",
}


def field_packets_from_cursor(cursor: Any) -> Optional[Sequence]:
    """pymysql 字段包。DB-API description 没有字符集，TEXT/BLOB 必须从这里读。"""
    result = getattr(cursor, "_result", None)
    fields = getattr(result, "fields", None) if result is not None else None
    return fields or None


def _packet_charset_and_length(packet: Any) -> Tuple[Optional[int], Optional[int]]:
    if packet is None:
        return None, None
    if isinstance(packet, dict):
        charset = packet.get("charsetnr")
        length = packet.get("length")
    else:
        charset = getattr(packet, "charsetnr", None)
        length = getattr(packet, "length", None)
    try:
        charset_id = int(charset) if charset is not None else None
    except (TypeError, ValueError):
        charset_id = None
    try:
        column_length = int(length) if length is not None else None
    except (TypeError, ValueError):
        column_length = None
    return charset_id, column_length


def _blob_family_label(length: Optional[int], *, binary: bool) -> str:
    """MySQL 用列最大字节长度区分 tiny/blob/medium/long。"""
    size = length if isinstance(length, int) and length > 0 else 65535
    if size <= 255:
        return "tinyblob" if binary else "tinytext"
    if size <= 65535:
        return "blob" if binary else "text"
    if size <= 16777215:
        return "mediumblob" if binary else "mediumtext"
    return "longblob" if binary else "longtext"


def mysql_display_type(
    type_code: Any,
    *,
    charset: Optional[int] = None,
    length: Optional[int] = None,
    ds_type: str = "mysql",
) -> str:
    """把 MySQL 协议类型码还原成接近建表类型的展示名。

    Doris STRING 在协议里是 MYSQL_TYPE_BLOB，但字符集不是 binary(63)。
    不能把所有 252 都标成 blob，否则文本列会被格子当成二进制藏起来。
    """
    try:
        code = int(type_code)
    except (TypeError, ValueError):
        return str(type_code or "unknown")
    binary = charset == _MYSQL_BINARY_CHARSET
    doris = (ds_type or "").lower() == "doris"
    if code == _MYSQL_TYPE_BLOB:
        if charset is None:
            # 没有字符集时不能把 252 判成文本，否则真 BLOB 会被当成字符串。
            return "blob"
        if not binary:
            # Doris 没有 TEXT；STRING 走 BLOB 类型码 + 文本字符集。
            return "string" if doris else _blob_family_label(length, binary=False)
        return _blob_family_label(length, binary=True)
    if code in (_MYSQL_TYPE_VAR_STRING, 15):
        if charset is None:
            return "varchar"
        return "varbinary" if binary else "varchar"
    if code == _MYSQL_TYPE_STRING:
        if charset is None:
            return "string" if doris else "char"
        if binary:
            return "binary"
        # Doris 的 CHAR / 部分 VARCHAR / LARGEINT 都回 254，标成 char 会误导。
        return "string" if doris else "char"
    return _MYSQL_TYPE_NAMES.get(code, f"type_{code}")


def column_types_from_description(
    ds_type: str,
    description: Optional[Sequence],
    field_packets: Optional[Sequence] = None,
) -> List[str]:
    """从 DB-API cursor.description 提取列类型展示名。"""
    if not description:
        return []
    lt = (ds_type or "").lower()
    out: List[str] = []
    if lt in ("mysql", "doris"):
        packets = list(field_packets or [])
        for index, col in enumerate(description):
            code = col[1] if len(col) > 1 else None
            charset, length = _packet_charset_and_length(packets[index] if index < len(packets) else None)
            out.append(mysql_display_type(code, charset=charset, length=length, ds_type=lt))
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


def semantic_type(raw_type: str) -> str:
    normalized = (raw_type or "").lower()
    if any(token in normalized for token in ("int", "decimal", "numeric", "float", "double", "real")):
        return "number"
    if "bool" in normalized or normalized == "bit":
        return "boolean"
    if any(token in normalized for token in ("date", "time")):
        return "datetime"
    if any(token in normalized for token in ("json", "array", "map", "struct")):
        return "json"
    if any(token in normalized for token in ("binary", "blob", "bytea")):
        return "binary"
    return "string"


def column_fields_from_description(
    ds_type: str,
    description: Optional[Sequence],
    field_packets: Optional[Sequence] = None,
) -> List[Dict[str, Any]]:
    """Build a portable DB-API column schema for MySQL, Doris and PostgreSQL."""
    if not description:
        return []
    raw_types = column_types_from_description(ds_type, description, field_packets)
    fields: List[Dict[str, Any]] = []
    for index, column in enumerate(description):
        raw_type = raw_types[index] if index < len(raw_types) else "unknown"
        precision = column[4] if len(column) > 4 else None
        scale = column[5] if len(column) > 5 else None
        null_ok = column[6] if len(column) > 6 else None
        fields.append(
            {
                "name": str(column[0]),
                "raw_type": raw_type,
                "semantic_type": semantic_type(raw_type),
                "nullable": bool(null_ok) if null_ok is not None else None,
                "precision": int(precision) if precision is not None else None,
                "scale": int(scale) if scale is not None else None,
            }
        )
    return fields


def result_set_from_cursor(
    ds_type: str,
    description: Optional[Sequence],
    rows: List,
    limit: int,
    field_packets: Optional[Sequence] = None,
    cursor: Any = None,
) -> dict:
    packets = field_packets if field_packets is not None else field_packets_from_cursor(cursor)
    cols = [d[0] for d in description] if description else []
    types = column_types_from_description(ds_type, description, packets)
    capped = rows[:limit]
    return {
        "columns": cols,
        "column_types": types,
        "fields": column_fields_from_description(ds_type, description, packets),
        "rows": [[json_cell_value(v) for v in row] for row in capped],
        "total": len(rows),
        "truncated": len(rows) >= limit,
    }

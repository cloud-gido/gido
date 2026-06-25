# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Stream SQL 预览：允许 SET / 临时表 DDL / SELECT，禁止写操作。"""
from __future__ import annotations

import re

from fastapi import HTTPException

from app.services.sql_readonly import _strip_sql_comments, split_sql_statements


def _first_keyword(cleaned: str) -> str:
    m = re.match(r"^(\w+)", cleaned.strip(), re.IGNORECASE)
    return (m.group(1) if m else "").upper()


def _assert_no_write_keywords(cleaned: str, *, allow_create_table: bool = False) -> None:
    up = f" {_strip_sql_comments(cleaned).upper()} "
    forbidden = (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "MERGE ",
        "DROP ",
        "ALTER ",
        "TRUNCATE ",
        "GRANT ",
        "REVOKE ",
        "CALL ",
        "EXECUTE ",
        "EXEC ",
        "REPLACE ",
    )
    if not allow_create_table:
        forbidden = forbidden + ("CREATE ",)
    for kw in forbidden:
        if kw in up:
            raise HTTPException(status_code=400, detail=f"预览禁止写操作或 DDL: {kw.strip()}")
    if re.search(r"\bINTO\b", cleaned, re.IGNORECASE) and not re.match(
        r"^\s*CREATE\s+TABLE\b", cleaned, re.IGNORECASE
    ):
        raise HTTPException(status_code=400, detail="预览禁止使用 INTO（如 INSERT INTO / SELECT INTO）")


def _validate_select_like(stmt: str) -> None:
    core = stmt.strip().rstrip(";").strip()
    cleaned = _strip_sql_comments(core).strip()
    if not re.match(r"^(WITH|SELECT)\b", cleaned, re.IGNORECASE):
        raise HTTPException(status_code=400, detail="预览查询须以 SELECT 或 WITH…SELECT 开头")
    _assert_no_write_keywords(cleaned)


def parse_stream_preview_statements(sql: str) -> list[str]:
    parts = split_sql_statements(sql, max_parts=48)
    if not parts:
        raise HTTPException(status_code=400, detail="SQL 不能为空")
    has_select = False
    validated: list[str] = []
    for stmt in parts:
        core = stmt.strip().rstrip(";").strip()
        if not core:
            raise HTTPException(status_code=400, detail="存在空语句，请删除多余分号")
        cleaned = _strip_sql_comments(core).strip()
        kw = _first_keyword(cleaned)
        if kw == "SET":
            if not re.match(r"^SET\s+'[^']+'\s*=\s*'.*';?$", core, re.IGNORECASE | re.DOTALL):
                raise HTTPException(status_code=400, detail="预览仅支持 SET 'key' = 'value' 形式")
            validated.append(core)
            continue
        if kw == "CREATE" and re.match(r"^CREATE\s+CATALOG\b", cleaned, re.IGNORECASE):
            _assert_no_write_keywords(cleaned, allow_create_table=True)
            validated.append(core)
            continue
        if kw == "CREATE" and re.match(r"^CREATE\s+TABLE\b", cleaned, re.IGNORECASE):
            _assert_no_write_keywords(cleaned, allow_create_table=True)
            validated.append(core)
            continue
        if kw == "USE":
            validated.append(core)
            continue
        if kw in ("WITH", "SELECT"):
            _validate_select_like(core)
            has_select = True
            validated.append(core)
            continue
        raise HTTPException(
            status_code=400,
            detail=f"预览不支持的语句类型（{kw or '未知'}）；仅允许 SET、CREATE TABLE/CATALOG、USE、SELECT",
        )
    if not has_select:
        raise HTTPException(status_code=400, detail="预览须包含至少一条 SELECT 或 WITH…SELECT")
    return validated

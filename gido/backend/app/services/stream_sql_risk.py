# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic, dependency-free risk analysis for executable Flink SQL."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from fastapi import HTTPException


_LEVEL_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class _Token:
    kind: str
    text: str

    @property
    def upper(self) -> str:
        return self.text.upper() if self.kind == "word" else ""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scan(sql: str) -> List[List[_Token]]:
    """Tokenize SQL and split statements without exposing quoted text as keywords."""
    statements: List[List[_Token]] = [[]]
    i, size = 0, len(sql)
    while i < size:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < size else ""
        if ch.isspace():
            i += 1
            continue
        if ch == "-" and nxt == "-":
            i += 2
            while i < size and sql[i] not in "\r\n":
                i += 1
            continue
        if ch == "#":
            i += 1
            while i < size and sql[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i < size:
                if sql[i] == "*" and i + 1 < size and sql[i + 1] == "/":
                    i += 2
                    break
                i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            kind = "identifier" if quote == "`" else "string"
            i += 1
            value: List[str] = []
            while i < size:
                current = sql[i]
                if current == "\\" and i + 1 < size:
                    value.append(sql[i + 1])
                    i += 2
                    continue
                if current == quote:
                    if i + 1 < size and sql[i + 1] == quote:
                        value.append(quote)
                        i += 2
                        continue
                    i += 1
                    break
                value.append(current)
                i += 1
            statements[-1].append(_Token(kind, "".join(value)))
            continue
        if ch == ";":
            if statements[-1]:
                statements.append([])
            i += 1
            continue
        if ch.isalnum() or ch in ("_", "$", "-"):
            start = i
            i += 1
            while i < size and (sql[i].isalnum() or sql[i] in ("_", "$", "-")):
                i += 1
            statements[-1].append(_Token("word", sql[start:i]))
            continue
        statements[-1].append(_Token("symbol", ch))
        i += 1
    return [statement for statement in statements if statement]


def _word(tokens: Sequence[_Token], index: int, value: str) -> bool:
    return index < len(tokens) and tokens[index].upper == value


def _object_name(tokens: Sequence[_Token], start: int) -> Optional[str]:
    parts: List[str] = []
    i = start
    while i < len(tokens):
        token = tokens[i]
        if token.kind in ("word", "identifier"):
            parts.append(token.text)
            i += 1
            if i < len(tokens) and tokens[i].text == ".":
                parts.append(".")
                i += 1
                continue
            break
        break
    value = "".join(parts).strip(".")
    return value or None


def _property_is(tokens: Sequence[_Token], key: str, value: str) -> bool:
    wanted_key, wanted_value = key.lower(), value.lower()
    for index, token in enumerate(tokens):
        if token.kind not in ("word", "string") or token.text.lower() != wanted_key:
            continue
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor].text in ("=", ">", ":"):
            cursor += 1
        if (
            cursor < len(tokens)
            and tokens[cursor].kind in ("word", "string")
            and tokens[cursor].text.lower() == wanted_value
        ):
            return True
    return False


def _qualified_catalog(object_name: Optional[str], current_catalog: Optional[str]) -> Optional[str]:
    if object_name and object_name.count(".") >= 2:
        return object_name.split(".", 1)[0].lower()
    return current_catalog.lower() if current_catalog else None


def _object_key(object_name: Optional[str], current_catalog: Optional[str]) -> Optional[str]:
    if not object_name:
        return None
    value = object_name.lower()
    if value.count(".") == 1 and current_catalog:
        return f"{current_catalog.lower()}.{value}"
    return value


def _build_assessment(sql_hash: str, risks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    risk_list = list(risks)
    code_counts: Dict[str, int] = {}
    for risk in risk_list:
        code = str(risk["code"])
        code_counts[code] = code_counts.get(code, 0) + 1
    for index, risk in enumerate(risk_list):
        code = str(risk["code"])
        risk["confirmation_code"] = (
            f"{code}:{int(risk['statement_index']) + 1}"
            if code_counts[code] > 1 and risk.get("statement_index") is not None
            else code
        )
    level = max(
        (risk["level"] for risk in risk_list),
        key=lambda item: _LEVEL_ORDER[item],
        default="low",
    )
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "level": level,
        "sql_hash": sql_hash,
        "requires_confirmation": any(
            bool(risk["requires_confirmation"]) for risk in risk_list
        ),
        "risks": risk_list,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    payload["assessment_hash"] = _sha256(canonical)
    return payload


def assess_stream_sql(
    sql: Optional[str],
    *,
    definition_kind: Optional[str] = None,
) -> Dict[str, Any]:
    content = sql or ""
    statements = _scan(content)
    paimon_catalogs = set()
    paimon_objects = set()
    current_catalog: Optional[str] = None
    risks: List[Dict[str, Any]] = []

    for statement_index, tokens in enumerate(statements):
        if _word(tokens, 0, "CREATE") and _word(tokens, 1, "CATALOG"):
            catalog = _object_name(tokens, 2)
            if catalog and _property_is(tokens, "type", "paimon"):
                paimon_catalogs.add(catalog.lower())
        if _word(tokens, 0, "USE") and _word(tokens, 1, "CATALOG"):
            current_catalog = _object_name(tokens, 2)
        if _word(tokens, 0, "CREATE"):
            cursor = 1
            if _word(tokens, cursor, "TEMPORARY"):
                cursor += 1
            if _word(tokens, cursor, "TABLE"):
                cursor += 1
                if (
                    _word(tokens, cursor, "IF")
                    and _word(tokens, cursor + 1, "NOT")
                    and _word(tokens, cursor + 2, "EXISTS")
                ):
                    cursor += 3
                created_name = _object_name(tokens, cursor)
                if created_name and _property_is(tokens, "connector", "paimon"):
                    paimon_objects.add(_object_key(created_name, current_catalog))

        code = level = message = statement_type = None
        object_name: Optional[str] = None
        temporary = False
        if _word(tokens, 0, "DROP"):
            cursor = 1
            if _word(tokens, cursor, "TEMPORARY"):
                temporary = True
                cursor += 1
            if cursor < len(tokens) and tokens[cursor].upper in (
                "TABLE",
                "CATALOG",
                "DATABASE",
            ):
                object_type = tokens[cursor].upper
                cursor += 1
                if _word(tokens, cursor, "IF") and _word(tokens, cursor + 1, "EXISTS"):
                    cursor += 2
                object_name = _object_name(tokens, cursor)
                code = f"DROP_{object_type}"
                statement_type = f"DROP {object_type}"
                level = "high" if temporary else "critical"
                message = f"{statement_type} 会删除{'临时' if temporary else ''}对象"
            else:
                object_type = (
                    tokens[cursor].upper
                    if cursor < len(tokens) and tokens[cursor].upper
                    else "OBJECT"
                )
                cursor += 1
                if object_type == "MATERIALIZED" and _word(tokens, cursor, "VIEW"):
                    object_type = "MATERIALIZED_VIEW"
                    cursor += 1
                if _word(tokens, cursor, "IF") and _word(tokens, cursor + 1, "EXISTS"):
                    cursor += 2
                object_name = _object_name(tokens, cursor)
                safe_type = "".join(
                    char if char.isalnum() else "_" for char in object_type
                )
                code = f"DROP_{safe_type or 'OBJECT'}"
                statement_type = f"DROP {object_type.replace('_', ' ')}"
                level = "high"
                message = f"{statement_type} 会删除对象"
        elif _word(tokens, 0, "TRUNCATE"):
            cursor = 2 if _word(tokens, 1, "TABLE") else 1
            object_name = _object_name(tokens, cursor)
            code, level = "TRUNCATE", "critical"
            statement_type, message = "TRUNCATE", "TRUNCATE 会清空对象数据"
        elif _word(tokens, 0, "ALTER") and _word(tokens, 1, "TABLE"):
            object_name = _object_name(tokens, 2)
            if any(token.upper == "DROP" for token in tokens[3:]):
                code, level = "ALTER_TABLE_DROP", "critical"
                statement_type, message = "ALTER TABLE DROP", "ALTER TABLE DROP 会删除表结构或分区"
        elif _word(tokens, 0, "DELETE") and _word(tokens, 1, "FROM"):
            object_name = _object_name(tokens, 2)
            code, level = "DELETE_FROM", "high"
            statement_type, message = "DELETE FROM", "DELETE FROM 会删除对象数据"
        elif _word(tokens, 0, "INSERT") and _word(tokens, 1, "OVERWRITE"):
            cursor = 2 + (1 if _word(tokens, 2, "TABLE") else 0)
            object_name = _object_name(tokens, cursor)
            code, level = "INSERT_OVERWRITE", "high"
            statement_type, message = "INSERT OVERWRITE", "INSERT OVERWRITE 会覆盖对象数据"
        elif (
            _word(tokens, 0, "CREATE")
            and _word(tokens, 1, "OR")
            and _word(tokens, 2, "REPLACE")
        ):
            cursor = 3
            object_type = tokens[cursor].upper if cursor < len(tokens) else "OBJECT"
            object_name = _object_name(tokens, cursor + 1)
            code, level = "CREATE_OR_REPLACE", "high"
            statement_type = f"CREATE OR REPLACE {object_type}"
            message = "CREATE OR REPLACE 会替换已有对象"

        if not code:
            continue
        catalog = _qualified_catalog(object_name, current_catalog)
        object_key = _object_key(object_name, current_catalog)
        is_paimon = bool(
            (definition_kind or "").lower() == "pipeline"
            or (catalog and catalog in paimon_catalogs)
            or object_key in paimon_objects
            or (code == "DROP_CATALOG" and (object_name or "").lower() in paimon_catalogs)
            or _property_is(tokens, "connector", "paimon")
            or _property_is(tokens, "type", "paimon")
        )
        risks.append(
            {
                "code": code,
                "level": level,
                "message": message,
                "statement_index": statement_index,
                "statement_type": statement_type,
                "object_name": object_name,
                "is_paimon": is_paimon,
                "requires_confirmation": True,
            }
        )
    return _build_assessment(_sha256(content), risks)


def combine_operation_risks(
    sql_assessment: Dict[str, Any],
    *,
    action: str,
    restore_mode: Optional[str] = None,
    allow_non_restored_state: bool = False,
) -> Dict[str, Any]:
    risks = [dict(risk) for risk in (sql_assessment.get("risks") or [])]
    normalized_action = (action or "").strip().lower()
    if normalized_action == "restart" and (restore_mode or "").strip().lower() == "stateless":
        risks.append(
            {
                "code": "STATELESS_RESTART",
                "level": "high",
                "message": "无状态重启会丢弃已有状态",
                "statement_index": None,
                "statement_type": "RESTART",
                "object_name": None,
                "is_paimon": False,
                "requires_confirmation": True,
            }
        )
    if allow_non_restored_state:
        risks.append(
            {
                "code": "ALLOW_NON_RESTORED_STATE",
                "level": "high",
                "message": "允许无法恢复的状态可能导致状态丢失",
                "statement_index": None,
                "statement_type": normalized_action.upper() or "OPERATION",
                "object_name": None,
                "is_paimon": False,
                "requires_confirmation": True,
            }
        )
    return _build_assessment(str(sql_assessment.get("sql_hash") or ""), risks)


def assert_risk_confirmation(
    assessment: Dict[str, Any],
    assessment_hash: Optional[str],
    confirmed_risk_codes: Optional[Sequence[str]],
) -> None:
    if not assessment.get("requires_confirmation"):
        return
    required = {
        str(risk.get("confirmation_code") or risk["code"])
        for risk in assessment.get("risks") or []
        if risk.get("requires_confirmation")
    }
    confirmed = {str(code) for code in (confirmed_risk_codes or [])}
    if assessment_hash != assessment.get("assessment_hash") or not required.issubset(
        confirmed
    ):
        raise HTTPException(status_code=409, detail=assessment)

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务返回字段契约：仅写元数据，不改变开放网关响应 JSON 结构。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence


def response_field_names(schema: Optional[Sequence[Any]]) -> List[str]:
    """从 response_fields / 列名列表提取有序字段名。"""
    if not schema:
        return []
    out: List[str] = []
    seen = set()
    for item in schema:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("alias") or "").strip()
        else:
            continue
        if not name or name in seen or name == "*":
            continue
        seen.add(name)
        out.append(name)
    return out


def merge_response_fields(
    existing: Optional[Sequence[Any]],
    columns: Sequence[str],
) -> List[Dict[str, Any]]:
    """用实测/推导列名刷新契约，保留原有 mask_type / alias。"""
    old_by_name: Dict[str, dict] = {}
    for item in existing or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("alias") or "").strip()
        if name:
            old_by_name[name] = dict(item)

    merged: List[Dict[str, Any]] = []
    seen = set()
    for col in columns:
        name = str(col or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        prev = old_by_name.get(name) or {}
        row = {"name": name}
        if prev.get("alias"):
            row["alias"] = prev["alias"]
        if prev.get("mask_type"):
            row["mask_type"] = prev["mask_type"]
        merged.append(row)
    return merged


def columns_from_wizard_config(wizard_config: Optional[dict]) -> List[str]:
    if not isinstance(wizard_config, dict):
        return []
    fields = wizard_config.get("fields") or []
    if fields == ["*"] or fields == "*":
        return []
    return response_field_names(fields if isinstance(fields, list) else [])


def response_fields_changed(
    existing: Optional[Sequence[Any]],
    new_schema: Sequence[dict],
) -> bool:
    return response_field_names(existing) != response_field_names(new_schema)


def build_list_item_openapi_schema(response_fields: Optional[Sequence[Any]]) -> dict:
    """OpenAPI：list 元素 schema。无契约时保持通用 object（与历史一致）。"""
    names = response_field_names(response_fields)
    if not names:
        return {"type": "object"}
    props = {n: {"type": "string", "description": n} for n in names}
    return {"type": "object", "properties": props}


def persist_response_fields_if_needed(db, api, columns: Sequence[str]) -> bool:
    """列名有变化时写回 api.response_fields。返回是否发生写入。

    不修改开放网关返回体；仅更新元数据行。
    """
    cols = [str(c).strip() for c in columns if str(c).strip()]
    if not cols:
        return False
    merged = merge_response_fields(getattr(api, "response_fields", None), cols)
    if not response_fields_changed(getattr(api, "response_fields", None), merged):
        return False
    api.response_fields = merged
    db.add(api)
    return True

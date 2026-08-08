# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务返回字段契约：元数据写入与开放响应隔离。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.data_api_schema import (
    build_list_item_openapi_schema,
    columns_from_wizard_config,
    merge_response_fields,
    persist_response_fields_if_needed,
    response_field_names,
)
from app.services.data_api_response import open_success_envelope


def test_response_field_names_from_dicts_and_strings():
    assert response_field_names(
        [{"name": "a"}, {"alias": "b"}, "c", {"name": "*"}, {"name": "a"}]
    ) == ["a", "b", "c"]


def test_merge_preserves_mask_and_alias():
    existing = [{"name": "id", "mask_type": "hash"}, {"name": "gone"}]
    merged = merge_response_fields(existing, ["id", "name"])
    assert merged == [
        {"name": "id", "mask_type": "hash"},
        {"name": "name"},
    ]


def test_columns_from_wizard_skips_star():
    assert columns_from_wizard_config({"fields": ["*"]}) == []
    assert columns_from_wizard_config({"fields": ["x", "y"]}) == ["x", "y"]


def test_openapi_item_schema_generic_without_contract():
    assert build_list_item_openapi_schema(None) == {"type": "object"}
    schema = build_list_item_openapi_schema([{"name": "foo"}])
    assert schema["properties"]["foo"]["type"] == "string"


def test_persist_only_when_names_change():
    db = MagicMock()
    api = SimpleNamespace(response_fields=[{"name": "a"}])
    assert persist_response_fields_if_needed(db, api, ["a"]) is False
    assert persist_response_fields_if_needed(db, api, ["a", "b"]) is True
    assert response_field_names(api.response_fields) == ["a", "b"]
    db.add.assert_called_once_with(api)


def test_open_envelope_never_includes_internal_columns_marker():
    data = {
        "list": [{"id": 1}],
        "TotalCount": 1,
        "PageNumber": 1,
        "PageSize": 10,
        "truncated": False,
        "cache_hit": False,
        "__gido_columns__": ["id"],
    }
    # 模拟网关：先 pop 再封包
    data.pop("__gido_columns__", None)
    body = open_success_envelope("t-1", data)
    assert "__gido_columns__" not in body
    assert "__gido_columns__" not in body.get("data", {})
    assert set(body.keys()) >= {"code", "success", "message", "trace_id", "data"}
    assert body["data"]["list"] == [{"id": 1}]

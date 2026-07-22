# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务开放响应格式与分页参数。"""
from app.services.data_api_response import (
    build_list_page_data,
    open_error_envelope,
    open_success_envelope,
    pop_pagination_params,
    rows_to_object_list,
)


def test_rows_to_object_list():
    cols = ["id", "name"]
    rows = [[1, "a"], [2, "b"]]
    assert rows_to_object_list(cols, rows) == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


def test_build_list_page_data():
    data = build_list_page_data(
        columns=["id", "name"],
        rows=[[1, "a"]],
        total=25,
        page=2,
        page_size=10,
        truncated=False,
        cache_hit=True,
    )
    assert data["list"] == [{"id": 1, "name": "a"}]
    assert data["total"] == 25
    assert data["page"] == 2
    assert data["pageSize"] == 10
    assert data["totalPages"] == 3
    assert data["cache_hit"] is True
    assert "columns" not in data
    assert "rows" not in data


def test_build_list_page_data_empty_total_pages():
    data = build_list_page_data(columns=[], rows=[], total=0, page=1, page_size=20)
    assert data["list"] == []
    assert data["totalPages"] == 0


def test_open_envelopes():
    ok = open_success_envelope("abc", {"list": [], "total": 0, "page": 1, "pageSize": 20, "totalPages": 0})
    assert ok["code"] == 0
    assert ok["success"] is True
    assert ok["message"] == "success"
    assert ok["trace_id"] == "abc"
    assert ok["data"]["list"] == []

    err = open_error_envelope("无效的应用凭证", http_status=401, trace_id="t1")
    assert err["code"] == 401
    assert err["success"] is False
    assert err["message"] == "无效的应用凭证"
    assert err["trace_id"] == "t1"
    assert err["data"] is None


def test_pop_pagination_params_prefers_page_page_size():
    raw = {"fixture_id": "FX001", "page": "2", "pageSize": "50", "page_no": "9"}
    page, size = pop_pagination_params(raw)
    assert page == 2
    assert size == 50
    assert raw == {"fixture_id": "FX001"}


def test_pop_pagination_params_legacy_snake():
    raw = {"page_no": "3", "page_size": "15"}
    page, size = pop_pagination_params(raw)
    assert page == 3
    assert size == 15
    assert raw == {}

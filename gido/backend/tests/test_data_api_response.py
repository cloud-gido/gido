# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务开放响应格式与分页参数（阿里云页码分页）。"""
from app.services.data_api_response import (
    build_list_page_data,
    open_error_envelope,
    open_success_envelope,
    pop_pagination_params,
    rows_to_object_list,
    wrap_count_sql,
)


def test_rows_to_object_list():
    cols = ["id", "name"]
    rows = [[1, "a"], [2, "b"]]
    assert rows_to_object_list(cols, rows) == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]


def test_build_list_page_data_aliyun_fields():
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
    assert data["TotalCount"] == 25
    assert data["PageNumber"] == 2
    assert data["PageSize"] == 10
    assert data["cache_hit"] is True
    assert "total" not in data
    assert "page" not in data
    assert "pageSize" not in data
    assert "totalPages" not in data
    assert "columns" not in data
    assert "rows" not in data


def test_build_list_page_data_empty():
    data = build_list_page_data(columns=[], rows=[], total=0, page=1, page_size=20)
    assert data["list"] == []
    assert data["TotalCount"] == 0
    assert data["PageNumber"] == 1
    assert data["PageSize"] == 20


def test_open_envelopes():
    ok = open_success_envelope(
        "abc",
        {"list": [], "TotalCount": 0, "PageNumber": 1, "PageSize": 20},
    )
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


def test_pop_pagination_params_prefers_aliyun():
    raw = {
        "fixture_id": "FX001",
        "PageNumber": "2",
        "PageSize": "50",
        "page": "9",
        "pageSize": "99",
    }
    page, size = pop_pagination_params(raw)
    assert page == 2
    assert size == 50
    assert raw == {"fixture_id": "FX001"}


def test_pop_pagination_params_aliases():
    raw = {"page": "3", "pageSize": "15"}
    page, size = pop_pagination_params(raw)
    assert page == 3
    assert size == 15
    assert raw == {}


def test_wrap_count_sql():
    sql = "SELECT id, name FROM t WHERE x = 1"
    assert wrap_count_sql(sql) == (
        "SELECT COUNT(*) AS _dw_api_cnt FROM (SELECT id, name FROM t WHERE x = 1) AS _dw_api_cnt_sub"
    )


def test_total_count_is_full_match_not_page_len():
    # 全量 12 条、本页 5 条 → TotalCount=12；页数由调用方 ceil(12/5)=3
    data = build_list_page_data(
        columns=["id"],
        rows=[[i] for i in range(5)],
        total=12,
        page=1,
        page_size=5,
    )
    assert data["TotalCount"] == 12
    assert data["PageSize"] == 5
    assert len(data["list"]) == 5
    assert "totalPages" not in data

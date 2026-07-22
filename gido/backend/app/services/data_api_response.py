# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务开放网关响应信封与分页参数（无 DB 依赖，便于单测）。"""
from __future__ import annotations

import math
import uuid
from typing import Any, Dict, List, Optional, Tuple


def new_trace_id() -> str:
    return uuid.uuid4().hex


def rows_to_object_list(columns: List[str], rows: List[List[Any]]) -> List[Dict[str, Any]]:
    """将表格行转为主流 ``[{field: value}, ...]``。"""
    return [dict(zip(columns, row)) for row in rows]


def build_list_page_data(
    *,
    columns: List[str],
    rows: List[List[Any]],
    total: int,
    page: int,
    page_size: int,
    truncated: bool = False,
    cache_hit: bool = False,
) -> Dict[str, Any]:
    """开放网关 / 试跑共用的 data 载荷（对象数组 + 分页）。"""
    page = max(1, int(page or 1))
    page_size = max(1, int(page_size or 20))
    total_i = max(0, int(total or 0))
    total_pages = math.ceil(total_i / page_size) if total_i > 0 else 0
    return {
        "list": rows_to_object_list(columns, rows),
        "total": total_i,
        "page": page,
        "pageSize": page_size,
        "totalPages": total_pages,
        "truncated": bool(truncated),
        "cache_hit": bool(cache_hit),
    }


def open_success_envelope(trace_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": 0,
        "success": True,
        "message": "success",
        "trace_id": trace_id,
        "data": data,
    }


def open_error_envelope(
    message: str,
    *,
    http_status: int = 400,
    trace_id: Optional[str] = None,
    code: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "code": int(code if code is not None else http_status),
        "success": False,
        "message": message,
        "trace_id": trace_id or new_trace_id(),
        "data": None,
    }


def pop_pagination_params(raw_params: Dict[str, Any]) -> Tuple[int, Optional[int]]:
    """从请求参数中取出分页；支持 page/pageSize（推荐）及 page_no/page_size。"""
    page_raw = None
    for key in ("page", "page_no", "pageNo"):
        if key in raw_params:
            val = raw_params.pop(key)
            if page_raw is None:
                page_raw = val
    size_raw = None
    for key in ("pageSize", "page_size"):
        if key in raw_params:
            val = raw_params.pop(key)
            if size_raw is None:
                size_raw = val
    page = int(page_raw or 1)
    page_size = int(size_raw) if size_raw not in (None, "") else None
    return max(1, page), page_size

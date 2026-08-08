# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据服务开放网关系统测试（需真实环境；默认 skip）。

启用方式（示例）::

    export GIDO_SYS_BASE_URL=https://gido-test.example.com
    export GIDO_SYS_WS_ID=1
    export GIDO_SYS_API_CODE=your_api_code
    export GIDO_SYS_APP_KEY=...
    export GIDO_SYS_APP_SECRET=...
    # 可选：管理员 token，用于校验 /api/data-service/apis/{id}/contract
    export GIDO_SYS_ADMIN_TOKEN=...
    export GIDO_SYS_API_ID=123

    pytest tests/test_data_api_contract_system.py -m system -q
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.system

_REQUIRED = ("GIDO_SYS_BASE_URL", "GIDO_SYS_WS_ID", "GIDO_SYS_API_CODE", "GIDO_SYS_APP_KEY", "GIDO_SYS_APP_SECRET")


def _enabled() -> bool:
    return all(os.getenv(k) for k in _REQUIRED)


def _request(method: str, url: str, *, headers: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"_raw": raw}


@pytest.mark.skipif(not _enabled(), reason="未设置 GIDO_SYS_* 环境变量，跳过系统测试")
def test_live_open_gateway_envelope_and_no_internal_marker():
    base = os.environ["GIDO_SYS_BASE_URL"].rstrip("/")
    ws = os.environ["GIDO_SYS_WS_ID"]
    code = os.environ["GIDO_SYS_API_CODE"]
    url = f"{base}/api/open/v1/ws/{ws}/{code}"
    status, body = _request(
        "GET",
        url,
        headers={
            "X-App-Key": os.environ["GIDO_SYS_APP_KEY"],
            "X-App-Secret": os.environ["GIDO_SYS_APP_SECRET"],
        },
    )
    assert status == 200, body
    assert body.get("success") is True
    assert body.get("code") == 0
    assert set(body.keys()) >= {"code", "success", "message", "trace_id", "data"}
    assert "__gido_columns__" not in body
    data = body.get("data") or {}
    assert "__gido_columns__" not in data
    assert "list" in data
    assert "TotalCount" in data
    # 有数据时字段应可枚举；空 list 也算访问成功
    rows = data.get("list") or []
    if rows:
        assert isinstance(rows[0], dict)
        assert rows[0]


@pytest.mark.skipif(
    not (_enabled() and os.getenv("GIDO_SYS_ADMIN_TOKEN") and os.getenv("GIDO_SYS_API_ID")),
    reason="未设置 GIDO_SYS_ADMIN_TOKEN / GIDO_SYS_API_ID，跳过契约接口系统测试",
)
def test_live_admin_contract_matches_open_list_keys():
    base = os.environ["GIDO_SYS_BASE_URL"].rstrip("/")
    api_id = os.environ["GIDO_SYS_API_ID"]
    token = os.environ["GIDO_SYS_ADMIN_TOKEN"]
    status, contract = _request(
        "GET",
        f"{base}/api/data-service/apis/{api_id}/contract",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert status == 200, contract
    assert contract.get("public_open_path") == (
        f"/api/open/v1/ws/{contract['workspace_id']}/{contract['api_code']}"
    )

    open_url = f"{base}{contract['public_open_path']}"
    st2, body = _request(
        "GET",
        open_url,
        headers={
            "X-App-Key": os.environ["GIDO_SYS_APP_KEY"],
            "X-App-Secret": os.environ["GIDO_SYS_APP_SECRET"],
        },
    )
    assert st2 == 200, body
    rows = (body.get("data") or {}).get("list") or []
    names = contract.get("response_field_names") or []
    if rows and names:
        assert set(names) == set(rows[0].keys())
    elif not rows:
        # 空结果：契约可有可无，只要接口可达
        assert "response_field_names" in contract

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""权限系统测试（需真实环境；默认 skip）。

启用::

    export GIDO_SYS_BASE_URL=https://gamelinelab-gido.envir.dev
    export GIDO_SYS_OPS_USER=ops
    export GIDO_SYS_OPS_PASSWORD=...
    export GIDO_SYS_DEV_USER=dev
    export GIDO_SYS_DEV_PASSWORD=...
    export GIDO_SYS_WS_ID=1

    pytest tests/test_rbac_permission_system.py -m system -q
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.system

_REQUIRED = (
    "GIDO_SYS_BASE_URL",
    "GIDO_SYS_OPS_USER",
    "GIDO_SYS_OPS_PASSWORD",
    "GIDO_SYS_DEV_USER",
    "GIDO_SYS_DEV_PASSWORD",
    "GIDO_SYS_WS_ID",
)


def _enabled() -> bool:
    return all(os.getenv(k) for k in _REQUIRED)


def _request(method: str, url: str, *, headers=None, body=None):
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


def _login(base: str, user: str, password: str) -> str:
    st, body = _request(
        "POST",
        f"{base}/api/auth/login",
        body={"username": user, "password": password},
    )
    assert st == 200, body
    tok = body.get("access_token")
    assert tok
    return tok


@pytest.mark.skipif(not _enabled(), reason="未设置 GIDO_SYS_* 运维/开发账号，跳过权限系统测试")
def test_live_ops_denied_studio_write_dev_allowed():
    base = os.environ["GIDO_SYS_BASE_URL"].rstrip("/")
    ws = int(os.environ["GIDO_SYS_WS_ID"])
    ops = _login(base, os.environ["GIDO_SYS_OPS_USER"], os.environ["GIDO_SYS_OPS_PASSWORD"])
    dev = _login(base, os.environ["GIDO_SYS_DEV_USER"], os.environ["GIDO_SYS_DEV_PASSWORD"])

    st, body = _request(
        "POST",
        f"{base}/api/studio/nodes",
        headers={"Authorization": f"Bearer {ops}"},
        body={"workspace_id": ws, "name": "sys_ops_should_fail", "node_type": "SQL", "script_content": "SELECT 1"},
    )
    assert st == 403, body

    st2, body2 = _request(
        "POST",
        f"{base}/api/studio/nodes",
        headers={"Authorization": f"Bearer {dev}"},
        body={
            "workspace_id": ws,
            "name": f"sys_dev_ok_{os.getpid()}",
            "node_type": "SQL",
            "script_content": "SELECT 1",
        },
    )
    assert st2 == 200, body2

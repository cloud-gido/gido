# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""从 GIDO 注入的上下文文件加载数据源连接信息。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

ENV_CONTEXT_FILE = "GIDO_JOB_CONTEXT_FILE"
CONTEXT_PROTOCOL_FIELD = "context_protocol_version"


def _validate_context_protocol(data: Dict[str, Any]) -> None:
    """Validate versioned launcher contexts while accepting legacy local contexts."""
    declared = data.get(CONTEXT_PROTOCOL_FIELD)
    if declared is None:
        return
    # Keep this import local so context.py remains independently importable.
    from gido_job import CONTEXT_PROTOCOL_VERSION

    if (
        isinstance(declared, bool)
        or not isinstance(declared, int)
        or declared != CONTEXT_PROTOCOL_VERSION
    ):
        raise RuntimeError(
            "GIDO 作业上下文协议不兼容: "
            f"expected={CONTEXT_PROTOCOL_VERSION}, actual={declared!r}"
        )


def load_job_context() -> Optional[Dict[str, Any]]:
    """读取 ``GIDO_JOB_CONTEXT_FILE`` JSON；未设置则返回 None。"""
    path = (os.environ.get(ENV_CONTEXT_FILE) or "").strip()
    if not path:
        return None
    if not os.path.isfile(path):
        raise RuntimeError(f"GIDO 作业上下文文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError("GIDO 作业上下文格式无效（须为 JSON 对象）")
    _validate_context_protocol(data)
    return data


def mysql_protocol_user(ds_type: str, username: Optional[str]) -> str:
    """Doris 空用户 → root；与平台 datasource_mysql_user 对齐。"""
    raw = (username or "").strip()
    if raw:
        return raw
    if (ds_type or "").strip().lower() == "doris":
        return "root"
    return ""

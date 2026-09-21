# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""QUALITY 工作流节点：绑定参数与 Dolphin 回调路径。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-quality-node")

from app.models.workspace import TaskNode
from app.services.dolphin import _ds_callback_curl
from app.services.quality_node import normalize_quality_params, quality_target_from_node


def test_normalize_and_target():
    assert normalize_quality_params({"table_id": "12", "rule_id": ""}) == {"table_id": 12, "rule_id": None}
    assert normalize_quality_params({"rule_id": 9})["rule_id"] == 9
    node = TaskNode(name="q", node_type="QUALITY", params={"table_id": 5})
    assert quality_target_from_node(node) == (5, None)


def test_ds_quality_callback_uses_internal_path_and_fail():
    script = _ds_callback_curl("/api/quality/internal/tables/42/check?bizdate=$[yyyy-MM-dd-1]")
    assert "/api/quality/internal/tables/42/check" in script
    assert "bizdate=$[yyyy-MM-dd-1]" in script
    assert "Authorization: Bearer $token" in script
    assert "--fail" in script

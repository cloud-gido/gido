# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import pytest

from app.services.probe_tree_store import sanitize_probe_tree_state


def test_sanitize_probe_tree_keeps_scripts():
    out = sanitize_probe_tree_state({
        "folders": [{"id": "f1", "name": "tmp", "parentId": None}],
        "scripts": [{"id": "s1", "name": "q1", "folderId": "f1", "sql": "SELECT 1", "limit": 20}],
        "activeScriptId": "s1",
    })
    assert out["activeScriptId"] == "s1"
    assert out["scripts"][0]["sql"] == "SELECT 1"
    assert out["folders"][0]["name"] == "tmp"


def test_sanitize_probe_tree_requires_script():
    with pytest.raises(ValueError, match="至少保留"):
        sanitize_probe_tree_state({"folders": [], "scripts": []})

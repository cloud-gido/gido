# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""ops_timeline：实例中心 / 告警中心共用时间口径。"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.services.ops_timeline import resolve_alert_occurred_at, workflow_instance_list_order_by


def test_resolve_alert_occurred_at_prefers_node_then_workflow_then_created():
    created = datetime(2026, 9, 1, 10, 0, 0)
    wf_start = datetime(2026, 9, 14, 12, 0, 0)
    wf_end = datetime(2026, 9, 14, 12, 5, 0)
    node_start = datetime(2026, 9, 14, 12, 1, 0)
    node_end = datetime(2026, 9, 14, 12, 4, 0)

    alert = SimpleNamespace(created_at=created)
    assert resolve_alert_occurred_at(alert) == created
    assert resolve_alert_occurred_at(
        alert, SimpleNamespace(started_at=wf_start, finished_at=None)
    ) == wf_start
    assert resolve_alert_occurred_at(
        alert,
        SimpleNamespace(started_at=wf_start, finished_at=wf_end),
        SimpleNamespace(started_at=node_start, finished_at=None),
    ) == wf_end
    assert resolve_alert_occurred_at(
        alert,
        SimpleNamespace(started_at=wf_start, finished_at=wf_end),
        SimpleNamespace(started_at=node_start, finished_at=node_end),
    ) == node_end


def test_workflow_instance_list_order_by_uses_started_at():
    clauses = workflow_instance_list_order_by()
    assert len(clauses) == 2
    # 编译成字符串应含 started_at，而非仅 created_at
    text = " ".join(str(c) for c in clauses).lower()
    assert "started_at" in text
    assert "created_at" not in text

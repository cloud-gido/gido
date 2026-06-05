# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""workflow_dag_validate 单元测试"""
import pytest

from app.services.workflow_dag_validate import assert_cron_when_scheduled, validate_dag_structure


def test_cron_requires_five_parts():
    assert_cron_when_scheduled("manual", None)
    with pytest.raises(ValueError, match="5 段"):
        assert_cron_when_scheduled("cron", "0 2 * *")
    with pytest.raises(ValueError, match="Cron"):
        assert_cron_when_scheduled("cron", "")


def test_dag_cycle_detected():
    with pytest.raises(ValueError, match="环路"):
        validate_dag_structure(
            {
                "nodes": [{"node_id": 1}, {"node_id": 2}],
                "edges": [{"source": 1, "target": 2}, {"source": 2, "target": 1}],
            }
        )


def test_dag_duplicate_node():
    with pytest.raises(ValueError, match="重复"):
        validate_dag_structure({"nodes": [{"node_id": 1}, {"node_id": 1}], "edges": []})


def test_dag_edge_unknown_node():
    with pytest.raises(ValueError, match="不在 DAG"):
        validate_dag_structure(
            {"nodes": [{"node_id": 1}], "edges": [{"source": 1, "target": 99}]}
        )

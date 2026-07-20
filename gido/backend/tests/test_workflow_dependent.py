# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""跨工作流 DEPENDENT：参数校验、Dolphin payload、本地实例检查。"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_dependent.db")

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services.workflow_dependent import (
    build_dependent_task_params,
    check_dependent_local,
    canonicalize_date_value,
    normalize_dependent_params,
    resolve_business_date,
    validate_dependent_node_params,
)


def _node(**kwargs):
    defaults = {
        "id": 1,
        "name": "dep-node",
        "node_type": "DEPENDENT",
        "workspace_id": 10,
        "params": {"depend_workflow_id": 20, "cycle": "day", "date_value": "today"},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _query_first(db: MagicMock, model, result):
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = result

    def _query(m, *args, **kwargs):
        if m is model:
            return q
        other = MagicMock()
        other.filter.return_value = other
        other.first.return_value = None
        return other

    db.query.side_effect = _query
    return q


def test_normalize_legacy_single_item():
    out = normalize_dependent_params(None)
    assert out["relation"] == "AND"
    assert len(out["depend_items"]) == 1
    assert out["depend_items"][0]["depend_workflow_id"] is None
    assert out["depend_items"][0]["date_value"] == "today"
    assert out["depend_items"][0]["cycle"] == "day"

    out2 = normalize_dependent_params({"date_value": "YESTERDAY", "depend_workflow_id": "12"})
    assert out2["depend_workflow_id"] == 12
    assert out2["date_value"] == "yesterday"
    assert out2["depend_items"][0]["depend_workflow_id"] == 12
    assert out2["depend_items"][0]["date_value"] == "yesterday"


def test_normalize_multi_items_and_hour():
    out = normalize_dependent_params(
        {
            "relation": "or",
            "depend_items": [
                {"depend_workflow_id": 1, "date_value": "currentHour"},
                {"depend_workflow_id": 2, "date_value": "last24Hours"},
            ],
        }
    )
    assert out["relation"] == "OR"
    assert out["depend_items"][0]["cycle"] == "hour"
    assert out["depend_items"][0]["date_value"] == "currentHour"
    assert out["depend_items"][1]["date_value"] == "last24Hours"
    assert out["depend_items"][1]["cycle"] == "hour"


def test_canonicalize_date_value():
    assert canonicalize_date_value("last1hour") == "last1Hour"
    assert canonicalize_date_value("thisWeek") == "thisWeek"
    assert canonicalize_date_value("nope") == "today"


def test_build_dependent_task_params_dep_task_code_zero():
    payload = build_dependent_task_params(
        project_code=100,
        definition_code=200,
        cycle="day",
        date_value="yesterday",
    )
    item = payload["dependence"]["dependTaskList"][0]["dependItemList"][0]
    assert item["depTaskCode"] == 0
    assert item["projectCode"] == 100
    assert item["definitionCode"] == 200
    assert item["dateValue"] == "yesterday"
    assert item["cycle"] == "day"


def test_build_dependent_multi_items_or():
    payload = build_dependent_task_params(
        project_code=9,
        relation="OR",
        items=[
            {"definition_code": 11, "date_value": "currentHour"},
            {"definition_code": 22, "date_value": "today"},
        ],
    )
    group = payload["dependence"]["dependTaskList"][0]
    assert group["relation"] == "OR"
    items = group["dependItemList"]
    assert len(items) == 2
    assert items[0]["depTaskCode"] == 0
    assert items[0]["dateValue"] == "currentHour"
    assert items[0]["cycle"] == "hour"
    assert items[1]["dateValue"] == "today"
    assert items[1]["cycle"] == "day"
    assert items[1]["definitionCode"] == 22


def test_resolve_business_date():
    now = datetime(2026, 7, 20, 15, 0, 0)
    assert resolve_business_date("today", now=now) == "2026-07-20"
    assert resolve_business_date("yesterday", now=now) == "2026-07-19"


def test_validate_missing_depend_workflow_id():
    db = MagicMock()
    node = _node(params={})
    with pytest.raises(ValueError, match="depend_workflow_id"):
        validate_dependent_node_params(db, node=node)


def test_validate_self_dependency():
    from app.models.workspace import Workflow

    db = MagicMock()
    target = SimpleNamespace(id=5, workspace_id=10, name="self", scheduler_definition_id="1")
    _query_first(db, Workflow, target)
    node = _node(params={"depend_workflow_id": 5})
    with pytest.raises(ValueError, match="不能依赖当前工作流自身"):
        validate_dependent_node_params(db, node=node, current_workflow_id=5)


def test_validate_cross_workspace():
    from app.models.workspace import Workflow

    db = MagicMock()
    target = SimpleNamespace(id=20, workspace_id=99, name="other-ws", scheduler_definition_id="1")
    _query_first(db, Workflow, target)
    node = _node(workspace_id=10, params={"depend_workflow_id": 20})
    with pytest.raises(ValueError, match="同一工作空间"):
        validate_dependent_node_params(db, node=node)


def test_validate_require_published_target():
    from app.models.workspace import Workflow

    db = MagicMock()
    target = SimpleNamespace(id=20, workspace_id=10, name="wf-b", scheduler_definition_id="")
    _query_first(db, Workflow, target)
    node = _node(params={"depend_workflow_id": 20})
    with pytest.raises(ValueError, match="请先发布"):
        validate_dependent_node_params(db, node=node, require_published_target=True)


def test_check_dependent_local_success_and_fail():
    from app.models.workspace import Workflow, WorkflowInstance

    db = MagicMock()
    target = SimpleNamespace(id=20, workspace_id=10, name="wf-b", scheduler_definition_id="99")
    node = _node(params={"depend_workflow_id": 20, "date_value": "today"})

    wf_q = MagicMock()
    wf_q.filter.return_value = wf_q
    wf_q.first.return_value = target
    inst_q = MagicMock()
    inst_q.filter.return_value = inst_q
    inst_q.first.return_value = SimpleNamespace(id=1, status="success")

    def query_ok(m, *a, **k):
        if m is Workflow:
            return wf_q
        if m is WorkflowInstance:
            return inst_q
        other = MagicMock()
        other.filter.return_value = other
        other.first.return_value = None
        return other

    db.query.side_effect = query_ok
    ok, logs = check_dependent_local(db, node, business_date="2026-07-20")
    assert ok is True
    assert any("依赖通过" in x for x in logs)

    inst_q.first.return_value = None
    ok2, logs2 = check_dependent_local(db, node, business_date="2026-07-20")
    assert ok2 is False
    assert any("未满足" in x or "未找到" in x for x in logs2)


def test_check_dependent_local_or_relation():
    from app.models.workspace import Workflow, WorkflowInstance

    db = MagicMock()
    t1 = SimpleNamespace(id=20, workspace_id=10, name="a", scheduler_definition_id="1")
    t2 = SimpleNamespace(id=21, workspace_id=10, name="b", scheduler_definition_id="2")
    node = _node(
        params={
            "relation": "OR",
            "depend_items": [
                {"depend_workflow_id": 20, "date_value": "today"},
                {"depend_workflow_id": 21, "date_value": "today"},
            ],
        }
    )

    call_wf = {"n": 0}
    call_inst = {"n": 0}

    def query_side(m, *a, **k):
        q = MagicMock()
        q.filter.return_value = q
        if m is Workflow:
            def first_wf():
                call_wf["n"] += 1
                return t1 if call_wf["n"] % 2 == 1 else t2
            q.first.side_effect = first_wf
            return q
        if m is WorkflowInstance:
            def first_inst():
                call_inst["n"] += 1
                # validate 不查 Instance；check 时第 1 条失败、第 2 条成功
                if call_inst["n"] == 1:
                    return None
                return SimpleNamespace(id=2, status="success")
            q.first.side_effect = first_inst
            return q
        q.first.return_value = None
        return q

    db.query.side_effect = query_side
    ok, logs = check_dependent_local(db, node, business_date="2026-07-20")
    assert ok is True
    assert any("relation=OR" in x for x in logs)

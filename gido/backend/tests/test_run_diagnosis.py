# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""运行诊断：「为什么这次没跑」要给出可执行的结论，而不是一句「没有实例」。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-diagnose")

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import rbac_models  # noqa: F401
from app.models.workspace import (
    NodeInstance,
    TaskNode,
    Workflow,
    WorkflowInstance,
    Workspace,
)
from app.services.run_diagnosis import diagnose_workflow_run

_NOW = datetime(2026, 9, 11, 4, 0, 0)  # UTC，即上海 12:00
_BIZ = "2026-09-10"


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def healthy_collector():
    """默认假设采集正常，避免每个用例都掺进一条采集告警。"""
    with patch(
        "app.services.run_collector.collector_health",
        return_value={"enabled": True, "stale": False, "lag_seconds": 3},
    ):
        yield


def _workflow(db, **kwargs) -> Workflow:
    ws = db.query(Workspace).first()
    if ws is None:
        ws = Workspace(name="infras", timezone="Asia/Shanghai")
        db.add(ws)
        db.commit()
        db.refresh(ws)
    defaults = dict(
        name="ads_daily", workspace_id=ws.id, status="published", is_active=True,
        schedule_type="cron", cron_expression="0 2 * * *", dag_config={"nodes": [], "edges": []},
    )
    defaults.update(kwargs)
    wf = Workflow(**defaults)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


def _codes(result) -> list[str]:
    return [f["code"] for f in result["findings"]]


def _run(db, wf, **kwargs):
    return diagnose_workflow_run(db, workflow=wf, business_date=_BIZ, now=_NOW, **kwargs)


def test_paused_schedule_is_the_headline(db):
    """被暂停是最常见的「为什么没跑」，且必须排在第一条。"""
    wf = _workflow(db, status="paused")
    result = _run(db, wf)

    assert "schedule_paused" in _codes(result)
    assert result["findings"][0]["level"] == "blocker"
    assert result["verdict"] == "周期调度已暂停"
    assert "恢复调度" in result["findings"][0]["action"]


def test_manual_only_workflow_explains_no_cron(db):
    wf = _workflow(db, schedule_type="manual", cron_expression=None)
    result = _run(db, wf)

    codes = _codes(result)
    assert "no_cron_schedule" in codes
    assert "no_instance" in codes


def test_missing_cron_on_scheduled_workflow(db):
    wf = _workflow(db, schedule_type="cron", cron_expression="   ")
    assert "cron_missing" in _codes(_run(db, wf))


def test_no_instance_includes_next_run_time(db):
    """没有实例时告诉运维下一次什么时候跑，判断要不要手工补。"""
    wf = _workflow(db)
    result = _run(db, wf)

    codes = _codes(result)
    assert "no_instance" in codes
    assert "next_run" in codes
    nxt = next(f for f in result["findings"] if f["code"] == "next_run")
    # 上海时间 9/11 12:00 之后的下一次 02:00 是 9/12
    assert "2026-09-12 02:00" in nxt["detail"]


def test_failed_instance_names_the_failed_node(db):
    wf = _workflow(db)
    node = TaskNode(workspace_id=wf.workspace_id, name="dwd_order", node_type="SQL")
    db.add(node)
    db.commit()
    db.refresh(node)
    inst = WorkflowInstance(
        workflow_id=wf.id, status="failed", business_date=_BIZ, trigger_type="schedule",
        started_at=_NOW - timedelta(minutes=30), finished_at=_NOW - timedelta(minutes=10),
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    db.add(NodeInstance(workflow_instance_id=inst.id, node_id=node.id, status="failed"))
    db.commit()

    result = _run(db, wf)
    top = result["findings"][0]
    assert top["code"] == "instance_failed"
    assert "dwd_order" in top["detail"]
    assert "置成功" in top["action"]


def test_success_does_not_report_upstream_problems(db):
    """已经成功了就不该再报「在等上游」，那只会把人带偏。"""
    wf = _workflow(db)
    dep_target = _workflow(db, name="upstream_wf")
    dep_node = TaskNode(
        workspace_id=wf.workspace_id, name="wait_upstream", node_type="DEPENDENT",
        params={"relation": "AND", "depend_items": [
            {"depend_workflow_id": dep_target.id, "date_value": "today"},
        ]},
    )
    db.add(dep_node)
    db.commit()
    db.refresh(dep_node)
    wf.dag_config = {"nodes": [{"node_id": dep_node.id, "name": "wait_upstream"}], "edges": []}
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="success", business_date=_BIZ, trigger_type="schedule",
        started_at=_NOW - timedelta(hours=2), finished_at=_NOW - timedelta(hours=1),
    ))
    db.commit()

    result = _run(db, wf)
    codes = _codes(result)
    assert "instance_success" in codes
    assert "dependency_unmet" not in codes


def test_unmet_dependency_points_at_the_upstream_workflow(db):
    wf = _workflow(db)
    dep_target = _workflow(db, name="upstream_wf")
    dep_node = TaskNode(
        workspace_id=wf.workspace_id, name="wait_upstream", node_type="DEPENDENT",
        params={"relation": "AND", "depend_items": [
            {"depend_workflow_id": dep_target.id, "date_value": "today"},
        ]},
    )
    db.add(dep_node)
    db.commit()
    db.refresh(dep_node)
    wf.dag_config = {"nodes": [{"node_id": dep_node.id, "name": "wait_upstream"}], "edges": []}
    db.commit()

    result = _run(db, wf)
    unmet = next(f for f in result["findings"] if f["code"] == "dependency_unmet")
    assert "wait_upstream" in unmet["title"]
    assert "upstream_wf" in unmet["detail"]
    assert unmet["level"] == "blocker"


def test_long_running_instance_flagged(db):
    wf = _workflow(db)
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="running", business_date=_BIZ, trigger_type="schedule",
        started_at=_NOW - timedelta(hours=9),
    ))
    db.commit()

    codes = _codes(_run(db, wf))
    assert "instance_in_progress" in codes
    assert "instance_long_running" in codes


def test_stale_collector_warns_before_declaring_it_never_ran(db):
    """采集落后时，「没有实例」可能只是还没同步到，必须说清楚。"""
    wf = _workflow(db)
    with patch(
        "app.services.run_collector.collector_health",
        return_value={"enabled": True, "stale": True, "lag_seconds": 600},
    ):
        result = _run(db, wf)
    stale = next(f for f in result["findings"] if f["code"] == "collector_stale")
    assert "600" in stale["detail"]


def test_healthy_workflow_says_so_instead_of_staying_silent(db):
    wf = _workflow(db)
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="success", business_date=_BIZ, trigger_type="schedule",
        started_at=_NOW - timedelta(hours=2), finished_at=_NOW - timedelta(hours=1),
    ))
    db.commit()

    result = _run(db, wf)
    assert "no_blocker_found" in _codes(result)
    assert all(f["level"] in ("ok", "info") for f in result["findings"])


def test_multiple_runs_prefers_the_successful_one(db):
    """同一业务日期先失败后重跑成功，结论应当是成功，不是失败。"""
    wf = _workflow(db)
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="failed", business_date=_BIZ, trigger_type="schedule",
        started_at=_NOW - timedelta(hours=4), finished_at=_NOW - timedelta(hours=4),
    ))
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="success", business_date=_BIZ, trigger_type="rerun",
        started_at=_NOW - timedelta(hours=2), finished_at=_NOW - timedelta(hours=1),
    ))
    db.commit()

    result = _run(db, wf)
    codes = _codes(result)
    assert "instance_success" in codes
    assert "instance_failed" not in codes
    assert "multiple_runs" in codes
    assert len(result["instance_ids"]) == 2


def test_manual_override_is_surfaced(db):
    wf = _workflow(db)
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="success", status_override="success",
        override_reason="数据已手工修复", business_date=_BIZ, trigger_type="schedule",
        started_at=_NOW - timedelta(hours=2), finished_at=_NOW - timedelta(hours=1),
    ))
    db.commit()

    result = _run(db, wf)
    override = next(f for f in result["findings"] if f["code"] == "status_override")
    assert "数据已手工修复" in override["detail"]


def test_business_date_defaults_to_latest_run(db):
    wf = _workflow(db)
    db.add(WorkflowInstance(
        workflow_id=wf.id, status="failed", business_date="2026-09-08", trigger_type="schedule",
    ))
    db.commit()

    result = diagnose_workflow_run(db, workflow=wf, now=_NOW)
    assert result["business_date"] == "2026-09-08"
    assert "instance_failed" in _codes(result)

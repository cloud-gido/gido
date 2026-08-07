# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""APS 工作流定时与 Dolphin 防双跑策略。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ["DATABASE_URL"] = "sqlite://"
os.environ.setdefault("DS_ENABLED", "false")
os.environ.setdefault("INTERNAL_TOKEN", "test-internal-token-aps")

from app.services.aps_workflow_schedule import (
    is_workflow_aps_eligible,
    is_workflow_ds_managed,
    resolve_aps_workflow_master_switch,
)


def test_ds_definition_blocks_aps():
    db = MagicMock()
    wf = SimpleNamespace(
        id=44,
        name="bigdata-checklist",
        workspace_id=1,
        is_active=True,
        schedule_type="cron",
        cron_expression="30 8 * * *",
        status="published",
        scheduler_definition_id="9001",
        scheduler_engine="dolphin",
    )
    managed, reason = is_workflow_ds_managed(db, wf)
    assert managed is True
    assert "definition" in reason
    ok, why = is_workflow_aps_eligible(db, wf)
    assert ok is False
    assert "Dolphin" in why or "definition" in why


def test_default_scheduler_engine_alone_does_not_block_aps():
    """scheduler_engine 默认 dolphin，草稿且无 definition 时仍可走 APS 兜底。"""
    db = MagicMock()
    wf = SimpleNamespace(
        id=3,
        name="draft-local",
        workspace_id=1,
        is_active=True,
        schedule_type="cron",
        cron_expression="0 3 * * *",
        status="draft",
        scheduler_definition_id=None,
        scheduler_engine="dolphin",
    )
    with patch(
        "app.services.aps_workflow_schedule.get_dolphin_runtime",
        return_value=SimpleNamespace(enabled=False),
    ), patch(
        "app.services.aps_workflow_schedule.resolve_aps_workflow_master_switch",
        return_value=(True, "auto"),
    ):
        managed, _ = is_workflow_ds_managed(db, wf)
        ok, why = is_workflow_aps_eligible(db, wf)
    assert managed is False
    assert ok is True
    assert why == "eligible"

def test_workspace_ds_enabled_blocks_aps():
    db = MagicMock()
    wf = SimpleNamespace(
        id=1,
        name="x",
        workspace_id=9,
        is_active=True,
        schedule_type="cron",
        cron_expression="0 * * * *",
        status="draft",
        scheduler_definition_id=None,
        scheduler_engine="dolphin",
    )
    with patch(
        "app.services.aps_workflow_schedule.get_dolphin_runtime",
        return_value=SimpleNamespace(enabled=True),
    ), patch(
        "app.services.aps_workflow_schedule.resolve_aps_workflow_master_switch",
        return_value=(True, "auto"),
    ):
        ok, why = is_workflow_aps_eligible(db, wf)
    assert ok is False
    assert "Dolphin" in why


def test_force_off_blocks_all():
    db = MagicMock()
    wf = SimpleNamespace(
        id=1,
        name="local",
        workspace_id=1,
        is_active=True,
        schedule_type="cron",
        cron_expression="0 2 * * *",
        scheduler_definition_id=None,
        scheduler_engine=None,
    )
    with patch(
        "app.services.aps_workflow_schedule.resolve_aps_workflow_master_switch",
        return_value=(False, "平台开关：已手动关闭 APS 工作流定时"),
    ):
        ok, why = is_workflow_aps_eligible(db, wf)
    assert ok is False
    assert "关闭" in why


def test_local_cron_eligible_without_ds():
    db = MagicMock()
    wf = SimpleNamespace(
        id=2,
        name="local-only",
        workspace_id=1,
        is_active=True,
        schedule_type="cron",
        cron_expression="0 2 * * *",
        scheduler_definition_id=None,
        scheduler_engine=None,
    )
    with patch(
        "app.services.aps_workflow_schedule.get_dolphin_runtime",
        return_value=SimpleNamespace(enabled=False),
    ), patch(
        "app.services.aps_workflow_schedule.resolve_aps_workflow_master_switch",
        return_value=(True, "auto"),
    ):
        ok, why = is_workflow_aps_eligible(db, wf)
    assert ok is True
    assert why == "eligible"


def test_master_switch_db_override_false():
    db = MagicMock()
    with patch(
        "app.services.aps_workflow_schedule._env_force",
        return_value=None,
    ), patch(
        "app.services.aps_workflow_schedule.get_aps_workflow_override",
        return_value=False,
    ):
        ok, reason = resolve_aps_workflow_master_switch(db)
    assert ok is False
    assert "手动关闭" in reason

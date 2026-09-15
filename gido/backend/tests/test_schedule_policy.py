# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.schedule_policy import (
    fail_retry_interval_minutes,
    normalize_failure_strategy,
    normalize_process_priority,
    normalize_worker_group,
    resolve_schedule_timezone,
    schedule_opts_from_workflow,
)
from app.services.dolphin import _fail_retry_interval


def test_fail_retry_interval_default_and_explicit_zero():
    assert fail_retry_interval_minutes({}) == 1
    assert fail_retry_interval_minutes({"retry_interval_minutes": None}) == 1
    assert fail_retry_interval_minutes({"retry_interval_minutes": ""}) == 1
    assert fail_retry_interval_minutes({"retry_interval_minutes": 0}) == 0
    assert fail_retry_interval_minutes({"retry_interval_minutes": 5}) == 5
    assert _fail_retry_interval({"retry_interval_minutes": 3}) == 3


def test_schedule_opts_from_workflow():
    class W:
        failure_strategy = "end"
        process_priority = "high"
        worker_group = " batch "
        schedule_timezone = None

    opts = schedule_opts_from_workflow(W(), workspace_timezone="Asia/Tokyo")
    assert opts["failure_strategy"] == "END"
    assert opts["process_priority"] == "HIGH"
    assert opts["worker_group"] == "batch"
    assert opts["timezone_id"] == "Asia/Tokyo"
    assert normalize_failure_strategy("nope") == "CONTINUE"
    assert normalize_process_priority("nope") == "MEDIUM"
    assert normalize_worker_group("") == "default"
    assert resolve_schedule_timezone(W(), "Asia/Shanghai") == "Asia/Shanghai"

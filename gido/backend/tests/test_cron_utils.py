# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.cron_utils import assert_linux_cron, linux_to_quartz_cron, preview_next_runs


def test_linux_to_quartz_both_star():
    assert linux_to_quartz_cron("* * * * *") == "0 * * * * ?"


def test_linux_to_quartz_day_only():
    assert linux_to_quartz_cron("0 8 1 * *") == "0 0 8 1 * ?"


def test_linux_to_quartz_week_only():
    assert linux_to_quartz_cron("0 0 * * 1") == "0 0 0 ? * 1"


def test_assert_linux_cron_rejects_bad():
    with pytest.raises(ValueError):
        assert_linux_cron("0 2 * *")
    with pytest.raises(ValueError):
        assert_linux_cron("99 99 * * *")


def test_preview_next_runs_daily_8am():
    base = datetime(2026, 7, 29, 9, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    linux, quartz, times = preview_next_runs("0 8 * * *", count=3, base=base)
    assert linux == "0 8 * * *"
    assert quartz == "0 0 8 * * ?"
    assert times == [
        "2026-07-30 08:00:00",
        "2026-07-31 08:00:00",
        "2026-08-01 08:00:00",
    ]

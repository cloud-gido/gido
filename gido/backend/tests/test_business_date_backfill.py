# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""补数据业务日 / scheduleTime / 宏相对业务日展开。"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_JOB_LIB = str(_BACKEND_ROOT / "python_job_lib")
if _JOB_LIB not in sys.path:
    sys.path.insert(0, _JOB_LIB)

from app.services.business_date import (
    bizdate_and_yesterday,
    complement_schedule_time_for_dolphin,
    normalize_business_date,
    schedule_time_for_dolphin,
)
from app.services.dolphin import _rewrite_sql_builtins
from gido_job.macros import substitute_sql_macros


def test_normalize_business_date_variants():
    assert normalize_business_date("2026-07-14") == "2026-07-14"
    assert normalize_business_date("2026-07-14 00:00:00") == "2026-07-14"
    assert normalize_business_date("20260714") == "2026-07-14"
    assert normalize_business_date("") is None
    assert normalize_business_date(None) is None


def test_schedule_time_for_dolphin_pads_midnight():
    assert schedule_time_for_dolphin("2026-07-14") == "2026-07-14 00:00:00"
    assert schedule_time_for_dolphin("2026-07-14 12:30:00") == "2026-07-14 00:00:00"


def test_complement_schedule_time_is_json_date_list():
    import json

    raw = complement_schedule_time_for_dolphin("2026-07-14")
    payload = json.loads(raw)
    assert payload == {"complementScheduleDateList": "2026-07-14 00:00:00"}


def test_bizdate_and_yesterday_relative():
    biz, yday = bizdate_and_yesterday("2026-07-14")
    assert biz == "2026-07-14"
    assert yday == "2026-07-13"


def test_rewrite_sql_builtins_maps_to_schedule_macros():
    sql = "select '${bizdate}', '${yesterday}'"
    out = _rewrite_sql_builtins(sql)
    assert "$[yyyy-MM-dd]" in out
    assert "$[yyyy-MM-dd-1]" in out
    assert "system.biz.date" not in out


def test_multi_day_macros_differ_per_bizdate():
    """补一周时，每天 $[yyyy-MM-dd-1] / ${yesterday} 必须随业务日变化。"""
    days = ["2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11", "2026-07-12", "2026-07-13", "2026-07-14"]
    sql = "B=${bizdate}|Y=${yesterday}|M=$[yyyy-MM-dd-1]"
    expanded = [substitute_sql_macros(sql, bizdate=d) for d in days]
    assert expanded == [
        "B=2026-07-08|Y=2026-07-07|M=2026-07-07",
        "B=2026-07-09|Y=2026-07-08|M=2026-07-08",
        "B=2026-07-10|Y=2026-07-09|M=2026-07-09",
        "B=2026-07-11|Y=2026-07-10|M=2026-07-10",
        "B=2026-07-12|Y=2026-07-11|M=2026-07-11",
        "B=2026-07-13|Y=2026-07-12|M=2026-07-12",
        "B=2026-07-14|Y=2026-07-13|M=2026-07-13",
    ]
    assert len(set(expanded)) == 7


def test_substitute_yesterday_follows_bizdate_not_wall_clock():
    out = substitute_sql_macros(
        "d='${yesterday}'",
        bizdate="2020-01-15",
        tz_name="Asia/Shanghai",
    )
    assert "2020-01-14" in out

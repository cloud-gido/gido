# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.date_macros import expand_date_macros_in_text, resolve_date_expr


def test_resolve_hour_offset_changes_calendar_day():
    assert resolve_date_expr("$[yyyy-MM-dd-1/24]", "2026-08-15") == "2026-08-14"
    assert resolve_date_expr("$[yyyy-MM-dd-8/24]", "2026-08-15") == "2026-08-14"
    assert resolve_date_expr("$[yyyy-MM-dd+1/24]", "2026-08-15") == "2026-08-15"
    assert resolve_date_expr("$[yyyy-MM-dd-1]", "2026-08-15") == "2026-08-14"


def test_expand_in_select_literal():
    out = expand_date_macros_in_text(
        "SELECT '$[yyyy-MM-dd-1/24]'",
        bizdate="2026-08-15",
    )
    assert out == "SELECT '2026-08-14'"
    assert "$[" not in out


def test_curly_braces_alias_same_as_square():
    out = expand_date_macros_in_text(
        "SELECT '${yyyy-MM-dd-1/24}'",
        bizdate="2026-08-15",
    )
    assert out == "SELECT '2026-08-14'"


def test_adhoc_without_bizdate_uses_wall_clock(monkeypatch):
    """试跑未传业务日：16:14 减 1 小时仍是当天日期。"""
    from datetime import datetime as real_dt
    from app.services import date_macros as dm

    class _DT:
        @staticmethod
        def now(tz=None):
            return real_dt(2026, 8, 15, 16, 14, 0)

        strptime = staticmethod(real_dt.strptime)

    monkeypatch.setattr(dm, "datetime", _DT)
    assert dm.resolve_date_expr("$[yyyy-MM-dd-1/24]") == "2026-08-15"
    assert dm.expand_date_macros_in_text("SELECT '${yyyy-MM-dd-1/24}'") == "SELECT '2026-08-15'"

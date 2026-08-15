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

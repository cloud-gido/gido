# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.adhoc_run_store import summarize_sql


def test_summarize_sql_strips_comments_and_whitespace():
    sql = """
    -- header
    SELECT id, name
    FROM ads_foo  -- trailing
    WHERE dt = '2026-01-01'
    """
    s = summarize_sql(sql)
    assert s is not None
    assert "SELECT id, name FROM ads_foo WHERE dt = '2026-01-01'" == s
    assert "--" not in s


def test_summarize_sql_truncates():
    sql = "SELECT " + ("x," * 80) + " y FROM t"
    s = summarize_sql(sql, max_len=40)
    assert s is not None
    assert len(s) <= 40
    assert s.endswith("…")


def test_summarize_sql_empty():
    assert summarize_sql(None) is None
    assert summarize_sql("   \n  -- only comment\n") is None

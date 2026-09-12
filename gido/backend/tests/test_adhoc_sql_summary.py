# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.adhoc_run_store import summarize_sql


def test_summarize_sql_select_table_style():
    sql = """
    -- header
    SELECT id, name, event, stime, ctime, log_id
    FROM ads_foo  -- trailing
    WHERE dt = '2026-01-01'
    """
    assert summarize_sql(sql) == "SELECT · ads_foo"


def test_summarize_sql_multi_statement():
    sql = "SELECT 1 FROM a; SELECT 2 FROM b;"
    assert summarize_sql(sql) == "SELECT · a 等2段"


def test_summarize_sql_truncates_long_non_table():
    sql = "SHOW " + ("X" * 100)
    s = summarize_sql(sql, max_len=40)
    assert s is not None
    assert len(s) <= 40
    assert s.startswith("SHOW")


def test_summarize_sql_empty():
    assert summarize_sql(None) is None
    assert summarize_sql("   \n  -- only comment\n") is None

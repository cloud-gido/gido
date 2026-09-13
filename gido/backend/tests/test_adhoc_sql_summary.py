# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.adhoc_run_store import preview_sql, summarize_sql


def test_summarize_sql_first_content_line():
    sql = """
    -- header
    SELECT id, name, event, stime, ctime, log_id
    FROM ads_foo  -- trailing
    WHERE dt = '2026-01-01'
    """
    assert summarize_sql(sql) == "SELECT id, name, event, stime, ctime, log_id"


def test_summarize_sql_truncates_long_first_line():
    sql = "SHOW " + ("X" * 100)
    s = summarize_sql(sql, max_len=40)
    assert s is not None
    assert len(s) <= 40
    assert s.startswith("SHOW")
    assert s.endswith("…")


def test_summarize_sql_empty():
    assert summarize_sql(None) is None
    assert summarize_sql("   \n  -- only comment\n") is None


def test_preview_sql_caps_lines_and_chars():
    lines = [f"SELECT {i} FROM t;" for i in range(50)]
    sql = "\n".join(lines)
    preview = preview_sql(sql, max_lines=5, max_chars=2048)
    assert preview is not None
    assert preview.count("\n") <= 5  # 4 newlines among 5 lines, plus trailing …
    assert "SELECT 0 FROM t;" in preview
    assert "…" in preview
    assert "SELECT 49 FROM t;" not in preview


def test_preview_sql_empty():
    assert preview_sql(None) is None
    assert preview_sql("   ") is None

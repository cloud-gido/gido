# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from app.services.sql_readonly import parse_readonly_statements


def test_parse_readonly_skips_block_comment_only_statement():
    sql = "/* SELECT 1; */; SELECT 2"
    parts = parse_readonly_statements(sql)
    assert len(parts) == 1
    assert parts[0].lower().startswith("select")
    assert "2" in parts[0]


def test_parse_readonly_supports_hash_line_comment():
    sql = "SELECT 1; \n# SELECT 2; \nSELECT 3;"
    parts = parse_readonly_statements(sql)
    assert len(parts) == 2
    assert parts[0].strip().lower().startswith("select")
    assert "1" in parts[0]
    assert "SELECT 3" in parts[1].upper()


def test_parse_readonly_supports_double_slash_line_comment():
    sql = "SELECT 1; \n// SELECT 2; \nSELECT 3;"
    parts = parse_readonly_statements(sql)
    assert len(parts) == 2
    assert "1" in parts[0]
    assert "3" in parts[1]


# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.sql_readonly import json_cell_value


def test_json_cell_value_keeps_js_safe_ints():
    assert json_cell_value(9007199254740991) == 9007199254740991
    assert json_cell_value(0) == 0
    assert json_cell_value(True) is True


def test_json_cell_value_stringifies_snowflake_bigint():
    snowflake = 302041836414177280
    assert json_cell_value(snowflake) == "302041836414177280"
    assert json_cell_value(-snowflake) == "-302041836414177280"

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.sql_readonly import column_fields_from_description, json_cell_value


def test_json_cell_value_keeps_js_safe_ints():
    assert json_cell_value(9007199254740991) == 9007199254740991
    assert json_cell_value(0) == 0
    assert json_cell_value(True) is True


def test_json_cell_value_stringifies_snowflake_bigint():
    snowflake = 302041836414177280
    assert json_cell_value(snowflake) == "302041836414177280"
    assert json_cell_value(-snowflake) == "-302041836414177280"


def test_json_cell_value_preserves_binary_as_base64():
    assert json_cell_value(b"\x01\x02\x03") == "base64:AQID"


def test_column_fields_include_portable_type_metadata():
    fields = column_fields_from_description(
        "test",
        [
            ("amount", "decimal", None, None, 20, 4, False),
            ("created_at", "timestamp", None, None, None, None, True),
            ("payload", "json", None, None, None, None, True),
        ],
    )
    assert fields == [
        {
            "name": "amount",
            "raw_type": "decimal",
            "semantic_type": "number",
            "nullable": False,
            "precision": 20,
            "scale": 4,
        },
        {
            "name": "created_at",
            "raw_type": "timestamp",
            "semantic_type": "datetime",
            "nullable": True,
            "precision": None,
            "scale": None,
        },
        {
            "name": "payload",
            "raw_type": "json",
            "semantic_type": "json",
            "nullable": True,
            "precision": None,
            "scale": None,
        },
    ]

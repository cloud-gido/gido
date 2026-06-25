# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import pytest
from fastapi import HTTPException

from app.services.stream_sql_preview import PREVIEW_MARKER, _aws_env_from_sql, _parse_preview_json
from app.services.stream_sql_preview_validate import parse_stream_preview_statements


def test_parse_stream_preview_allows_set_create_select():
    sql = """
SET 'execution.runtime-mode' = 'batch';
CREATE TABLE t (id BIGINT) WITH ('connector'='paimon', 'path'='file:///data/t');
SELECT * FROM t;
"""
    parts = parse_stream_preview_statements(sql)
    assert len(parts) == 3
    assert parts[-1].upper().startswith("SELECT")


def test_parse_stream_preview_rejects_insert():
    with pytest.raises(HTTPException) as exc:
        parse_stream_preview_statements("INSERT INTO t SELECT 1")
    assert exc.value.status_code == 400


def test_parse_stream_preview_requires_select():
    with pytest.raises(HTTPException):
        parse_stream_preview_statements("SET 'x' = 'y';")


def test_parse_preview_json_marker():
    logs = "INFO start\n" + PREVIEW_MARKER + '{"columns":["a"],"column_types":["INT"],"rows":[[1]],"total":1,"truncated":false}\n'
    out = _parse_preview_json(logs)
    assert out["columns"] == ["a"]
    assert out["rows"] == [[1]]
    assert out["total"] == 1


def test_aws_env_from_sql_s3a_settings():
    sql = """
SET 'fs.s3a.access.key' = 'AKIA_TEST';
SET 'fs.s3a.secret.key' = 'secret';
SET 'fs.s3a.endpoint' = 's3.us-east-2.amazonaws.com';
"""
    env = {item["name"]: item["value"] for item in _aws_env_from_sql(sql)}
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA_TEST"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert env["AWS_DEFAULT_REGION"] == "us-east-2"

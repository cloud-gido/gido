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


def test_prepare_preview_script_irsa_strips_static_keys(monkeypatch):
    from app.core.config import settings
    from app.services.stream_sql_preview import _prepare_preview_script

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True)
    monkeypatch.setattr(
        settings,
        "FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER",
        "com.amazonaws.auth.WebIdentityTokenCredentialsProvider",
    )
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_REGION", "sa-east-1")
    sql = """
SET 'execution.runtime-mode' = 'batch';
SET 'fs.s3a.access.key' = 'AKIA_TEST';
SET 'fs.s3a.aws.credentials.provider' = 'org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider';
CREATE TABLE t (id BIGINT) WITH ('connector'='paimon', 'path'='s3a://bucket/demo/t');
SELECT * FROM t;
"""
    out = _prepare_preview_script(sql)
    assert "fs.s3a.access.key" not in out
    assert "SimpleAWSCredentialsProvider" not in out
    assert "WebIdentityTokenCredentialsProvider" in out
    assert "sa-east-1" in out


def test_prepare_preview_script_keeps_static_keys_when_present(monkeypatch):
    from app.core.config import settings
    from app.services.stream_sql_preview import _prepare_preview_script

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True)
    sql = """
SET 'execution.runtime-mode' = 'batch';
SET 'fs.s3a.access.key' = 'AKIA_LOCAL';
SET 'fs.s3a.secret.key' = 'secret';
SET 'fs.s3a.aws.credentials.provider' = 'org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider';
CREATE TABLE t (id BIGINT) WITH ('connector'='paimon', 'path'='s3a://bucket/demo/t');
SELECT * FROM t;
"""
    out = _prepare_preview_script(sql)
    assert "AKIA_LOCAL" in out
    assert "SimpleAWSCredentialsProvider" in out
    assert "WebIdentityTokenCredentialsProvider" not in out


def test_aws_env_from_sql_s3a_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", False)
    sql = """
SET 'fs.s3a.access.key' = 'AKIA_TEST';
SET 'fs.s3a.secret.key' = 'secret';
SET 'fs.s3a.endpoint' = 's3.us-east-2.amazonaws.com';
"""
    env = {item["name"]: item["value"] for item in _aws_env_from_sql(sql)}
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA_TEST"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert env["AWS_DEFAULT_REGION"] == "us-east-2"


def test_preview_java_sys_props_irsa(monkeypatch):
    from app.core.config import settings
    from app.services.stream_sql_preview import _preview_java_sys_props, _preview_shell

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True)
    monkeypatch.setattr(
        settings,
        "FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER",
        "com.amazonaws.auth.WebIdentityTokenCredentialsProvider",
    )
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_REGION", "sa-east-1")
    sql = """
SET 'execution.runtime-mode' = 'batch';
CREATE TABLE t (id BIGINT) WITH ('connector'='paimon', 'path'='s3a://bucket/demo/t');
SELECT * FROM t;
"""
    props = _preview_java_sys_props(sql)
    assert "WebIdentityTokenCredentialsProvider" in props
    assert "sa-east-1" in props
    shell = _preview_shell(sql, 50)
    assert "java -Dfs.s3a.aws.credentials.provider=" in shell


def test_aws_env_irsa_region_from_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True)
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_REGION", "sa-east-1")
    sql = """
SET 'execution.runtime-mode' = 'batch';
CREATE TABLE t (id BIGINT) WITH ('connector'='paimon', 'path'='s3a://bucket/demo/t');
SELECT * FROM t;
"""
    env = {item["name"]: item["value"] for item in _aws_env_from_sql(sql)}
    assert env["AWS_DEFAULT_REGION"] == "sa-east-1"
    assert env["AWS_REGION"] == "sa-east-1"

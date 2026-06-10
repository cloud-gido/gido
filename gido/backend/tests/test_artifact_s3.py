# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
from unittest.mock import MagicMock, patch

from app.core.config import settings
from app.services import artifact_s3 as s3
from app.services import jar_artifact as ja
from app.services import sql_artifact as sql_a
from app.services.flink_operator_submit import effective_sql_source, resolve_jar_uri_for_job


def test_artifact_s3_prefix_prefers_flink_operator_setting(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://bucket-a/jars")
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_PREFIX", "s3://bucket-b/alt")
    assert s3.artifact_s3_prefix() == "s3://bucket-a/jars"


def test_artifact_s3_prefix_falls_back_to_gido_alias(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", None)
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_PREFIX", "s3://bucket-b/artifacts")
    assert s3.artifact_s3_prefix() == "s3://bucket-b/artifacts"


def test_build_s3_artifact_uri(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://acme-data/gido-artifacts")
    assert s3.build_s3_artifact_uri(42, "artifact.jar") == "s3://acme-data/gido-artifacts/42/artifact.jar"
    assert s3.build_s3_artifact_uri(42, "artifact.sql") == "s3://acme-data/gido-artifacts/42/artifact.sql"


def test_resolve_jar_uri_prefers_s3(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://acme/gido-artifacts")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://backend:8001")
    assert resolve_jar_uri_for_job(7) == "s3://acme/gido-artifacts/7/artifact.jar"


def test_resolve_jar_uri_http_fallback(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", None)
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_PREFIX", None)
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://backend:8001")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_ARTIFACT_TOKEN", "tok")
    uri = resolve_jar_uri_for_job(3)
    assert uri.startswith("http://backend:8001/api/streaming/jobs/3/artifact.jar?token=")


def test_effective_sql_source_defaults_to_s3_when_prefix_set(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://b/p")
    assert effective_sql_source("mount") == "s3"
    assert effective_sql_source("http") == "http"
    assert effective_sql_source(None) == "s3"


def test_effective_sql_source_mount_when_no_s3(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", None)
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_PREFIX", None)
    assert effective_sql_source("mount") == "mount"


@patch("app.services.artifact_s3._s3_client")
def test_upload_artifact_bytes(mock_client_fn, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://test-bucket/gido-artifacts")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3

    uri = s3.upload_artifact_bytes(9, "artifact.jar", b"PK\x03\x04", content_type="application/java-archive")
    assert uri == "s3://test-bucket/gido-artifacts/9/artifact.jar"
    mock_s3.put_object.assert_called_once()
    kwargs = mock_s3.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "test-bucket"
    assert kwargs["Key"] == "gido-artifacts/9/artifact.jar"
    assert kwargs["Body"] == b"PK\x03\x04"


@patch("app.services.artifact_s3._s3_client")
def test_save_jar_bytes_uploads_to_s3(mock_client_fn, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://test-bucket/jars")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3

    ja.save_jar_bytes(5, b"jar-content")
    assert ja.jar_artifact_exists(5)
    mock_s3.put_object.assert_called_once()


@patch("app.services.artifact_s3._s3_client")
def test_jar_artifact_exists_checks_s3_when_local_missing(mock_client_fn, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://test-bucket/jars")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3
    mock_s3.head_object.return_value = {}

    assert ja.jar_artifact_exists(11)
    mock_s3.head_object.assert_called_once_with(Bucket="test-bucket", Key="jars/11/artifact.jar")


@patch("app.services.artifact_s3._s3_client")
def test_save_sql_script_uploads_to_s3(mock_client_fn, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://test-bucket/artifacts")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3

    sql_a.save_sql_script(2, "SELECT 1;")
    assert sql_a.build_sql_s3_uri_for_operator(2) == "s3://test-bucket/artifacts/2/artifact.sql"
    mock_s3.put_object.assert_called_once()

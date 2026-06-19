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


def test_resolve_jar_uri_always_http_even_with_profile_s3(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://backend:8001")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_ARTIFACT_TOKEN", "tok")
    from app.services.operator_runtime import OperatorRuntimeContext

    ctx = OperatorRuntimeContext(
        profile_id=1,
        profile_name="cluster-a",
        namespace="flink",
        image="img:1",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081",
        cluster_domain="cluster.local",
        checkpoint_dir=None,
        image_pull_secrets=None,
        s3_auth_mode="static",
        s3_access_key_id="AKIA",
        s3_secret_access_key="secret",
        s3_session_token=None,
        jar_s3_prefix="s3://cluster-a-bucket/jars",
        s3_region=None,
        s3_endpoint_url=None,
    )
    uri = resolve_jar_uri_for_job(7, runtime_ctx=ctx)
    assert uri.startswith("http://backend:8001/api/streaming/jobs/7/artifact.jar?token=")


def test_artifact_s3_prefix_from_profile_over_platform(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://platform/default")
    ctx = OperatorRuntimeContext(
        profile_id=2,
        profile_name="b",
        namespace="flink",
        image="img",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://x",
        cluster_domain="cluster.local",
        checkpoint_dir=None,
        image_pull_secrets=None,
        s3_auth_mode=None,
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_session_token=None,
        jar_s3_prefix="s3://cluster-b/jars",
        s3_region=None,
        s3_endpoint_url=None,
    )
    assert s3.artifact_s3_prefix(ctx) == "s3://cluster-b/jars"
    assert s3.build_s3_artifact_uri(9, "artifact.jar", runtime_ctx=ctx) == (
        "s3://cluster-b/jars/9/artifact.jar"
    )


def test_resolve_jar_uri_http_when_s3_prefix_but_object_missing(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://acme/gido-artifacts")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://backend:8001")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_ARTIFACT_TOKEN", "tok")
    uri = resolve_jar_uri_for_job(7)
    assert uri.startswith("http://backend:8001/api/streaming/jobs/7/artifact.jar?token=")


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


def test_save_jar_bytes_local_only(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://test-bucket/jars")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))

    result = ja.save_jar_bytes(5, b"jar-content")
    assert result.path.is_file()
    assert ja.jar_artifact_exists(5)
    assert result.path == tmp_path / "5" / "artifact.jar"


def test_jar_artifact_exists_local_only(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    assert not ja.jar_artifact_exists(11)
    d = tmp_path / "11"
    d.mkdir()
    (d / "artifact.jar").write_bytes(b"x")
    assert ja.jar_artifact_exists(11)


@patch("app.services.artifact_s3._s3_client")
def test_save_sql_script_uploads_to_s3(mock_client_fn, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://test-bucket/artifacts")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3

    sql_a.save_sql_script(2, "SELECT 1;")
    assert sql_a.build_sql_s3_uri_for_operator(2) == "s3://test-bucket/artifacts/2/artifact.sql"
    mock_s3.put_object.assert_called_once()


@patch("app.services.artifact_s3._s3_client")
def test_list_s3_objects_under_key_prefix(mock_client_fn):
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3
    mock_s3.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "jars/7/artifact.jar", "Size": 1024, "LastModified": None, "ETag": '"abc"'},
            {"Key": "jars/7/", "Size": 0},
        ],
        "IsTruncated": False,
    }
    rows, err = s3.list_s3_objects_under_key_prefix("test-bucket", "jars/7")
    assert err is None
    assert len(rows) == 1
    assert rows[0]["key"] == "jars/7/artifact.jar"
    assert rows[0]["size_bytes"] == 1024


@patch("app.services.artifact_s3._s3_client")
def test_list_s3_job_folder_prefixes(mock_client_fn):
    mock_s3 = MagicMock()
    mock_client_fn.return_value = mock_s3
    mock_s3.list_objects_v2.return_value = {
        "CommonPrefixes": [
            {"Prefix": "jars/7/"},
            {"Prefix": "jars/42/"},
        ],
        "IsTruncated": False,
    }
    folders, err = s3.list_s3_job_folder_prefixes("test-bucket", "jars")
    assert err is None
    assert len(folders) == 2
    assert folders[0]["job_id"] == "7"
    assert folders[1]["uri"] == "s3://test-bucket/jars/42"


def test_jar_artifact_inventory(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://backend:8001")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_ARTIFACT_TOKEN", "tok")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    (tmp_path / "9").mkdir()
    (tmp_path / "9" / "artifact.jar").write_bytes(b"jar")

    inv = ja.jar_artifact_inventory(9)
    assert inv["job_id"] == 9
    assert inv["storage_mode"] == "local"
    assert inv["local_artifact"]["exists"] is True
    assert inv["artifact_ready"] is True
    assert inv["operator_jar_uri"].startswith("http://backend:8001/api/streaming/jobs/9/artifact.jar?token=")


def test_artifact_s3_prefix_ignores_unresolved_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "FLINK_OPERATOR_JAR_S3_PREFIX")
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_PREFIX", None)
    assert s3.artifact_s3_prefix() is None
    hint = s3.s3_prefix_config_hint()
    assert hint is not None
    assert "FLINK_OPERATOR_JAR_S3_PREFIX" in hint


def test_artifact_s3_prefix_profile_invalid_falls_back_to_platform(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://platform/jars")
    ctx = OperatorRuntimeContext(
        profile_id=1,
        profile_name="bad-profile",
        namespace="flink",
        image="img",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://x",
        cluster_domain="cluster.local",
        checkpoint_dir=None,
        image_pull_secrets=None,
        s3_auth_mode=None,
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_session_token=None,
        jar_s3_prefix="FLINK_OPERATOR_JAR_S3_PREFIX",
        s3_region=None,
        s3_endpoint_url=None,
    )
    assert s3.artifact_s3_prefix(ctx) == "s3://platform/jars"


def test_save_jar_bytes_preserves_original_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))

    result = ja.save_jar_bytes(8, b"jar-content", original_filename="my-flink-job-1.0.jar")
    assert result.storage_filename == "my-flink-job-1.0.jar"
    assert (tmp_path / "8" / "my-flink-job-1.0.jar").is_file()
    assert not (tmp_path / "8" / "artifact.jar").exists()
    assert ja.jar_artifact_exists(8, jar_path="my-flink-job-1.0.jar")


def test_jar_artifact_inventory_reports_missing_http_base(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "")
    monkeypatch.setattr(settings, "JAR_ARTIFACT_DIR", str(tmp_path))
    inv = ja.jar_artifact_inventory(1)
    assert inv["storage_mode"] == "local"
    assert inv["http_base"] is None
    assert inv["operator_jar_uri_error"] is not None

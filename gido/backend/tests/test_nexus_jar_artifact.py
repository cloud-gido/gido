# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.services import jar_artifact as ja
from app.services.nexus_artifact import validate_nexus_jar_url


def test_validate_nexus_jar_url_https_jar():
    url = "https://nexus.example.com/repository/releases/com/foo/app-1.0.jar"
    assert validate_nexus_jar_url(url) == url


def test_validate_nexus_jar_url_with_query_token():
    url = "https://nexus.example.com/repo/app-1.0.jar?token=temp-abc&expires=1710000000"
    assert validate_nexus_jar_url(url) == url


def test_validate_nexus_jar_url_rejects_non_jar():
    with pytest.raises(ValueError, match="\\.jar"):
        validate_nexus_jar_url("https://nexus.example.com/maven-metadata.xml")


def test_resolve_jar_delivery_mode():
    assert ja.resolve_jar_delivery_mode("v2_2") == "direct_s3"
    assert ja.resolve_jar_delivery_mode("v1_19") == "direct_s3"
    assert ja.resolve_jar_delivery_mode("v1_17") == "local_staging"


def test_resolve_jar_submit_artifacts_direct_s3():
    from app.services.operator_runtime import OperatorRuntimeContext

    ctx = OperatorRuntimeContext(
        profile_id=1,
        profile_name="p",
        namespace="flink",
        image="img:2.2.1",
        flink_version="v2_2",
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
        jar_s3_prefix="s3://bucket/jars",
        s3_region=None,
        s3_endpoint_url=None,
    )
    ref = ja.S3ArtifactRef(uri="s3://bucket/jars/9/artifact.jar", sha256="abc", skipped_upload=True)
    arts = ja.resolve_jar_submit_artifacts(9, runtime_ctx=ctx, s3_ref=ref)
    assert arts.delivery_mode == "direct_s3"
    assert arts.jar_uri == "s3://bucket/jars/9/artifact.jar"
    assert not arts.uses_local_staging


@patch("app.services.jar_artifact.presign_s3_artifact_get_url")
def test_resolve_jar_submit_artifacts_local_staging_presign(mock_presign):
    from app.services.operator_runtime import OperatorRuntimeContext

    mock_presign.return_value = "https://bucket.s3.amazonaws.com/jars/9/artifact.jar?X-Amz-Signature=abc"
    ctx = OperatorRuntimeContext(
        profile_id=1,
        profile_name="p",
        namespace="flink",
        image="img:1.17.2",
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
        jar_s3_prefix="s3://bucket/jars",
        s3_region=None,
        s3_endpoint_url=None,
    )
    ref = ja.S3ArtifactRef(uri="s3://bucket/jars/9/artifact.jar", sha256="abc")
    arts = ja.resolve_jar_submit_artifacts(9, runtime_ctx=ctx, s3_ref=ref)
    assert arts.delivery_mode == "local_staging"
    assert arts.jar_uri.startswith("local://")
    assert arts.staging_fetch_url == mock_presign.return_value
    assert arts.uses_local_staging


@patch("app.services.jar_artifact.upload_artifact_bytes")
@patch("app.services.jar_artifact.fetch_jar_bytes_from_nexus")
@patch("app.services.jar_artifact.artifact_exists_in_s3")
def test_ensure_jar_in_profile_s3_skip_upload(mock_exists, mock_fetch, mock_upload):
    from app.services.operator_runtime import OperatorRuntimeContext

    mock_fetch.return_value = b"PK\x03\x04" + b"x" * 10
    mock_exists.return_value = True
    ctx = OperatorRuntimeContext(
        profile_id=1,
        profile_name="p",
        namespace="flink",
        image="img",
        flink_version="v2_2",
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
        jar_s3_prefix="s3://bucket/jars",
        s3_region=None,
        s3_endpoint_url=None,
    )
    job = MagicMock()
    job.id = 9
    job.jar_path = None
    job.jar_nexus_url = "https://nexus.example.com/a.jar"
    job.jar_nexus_sha256 = ja._sha256_bytes(mock_fetch.return_value)

    with patch("app.services.jar_artifact.build_s3_artifact_uri", return_value="s3://bucket/jars/9/artifact.jar"):
        with patch("app.services.jar_artifact.save_jar_bytes") as mock_save:
            ref = ja.ensure_jar_in_profile_s3(job, ctx)
    mock_upload.assert_not_called()
    mock_save.assert_not_called()
    assert ref.skipped_upload is True


@patch("app.services.nexus_artifact.httpx.Client")
def test_fetch_jar_anonymous_no_basic_auth(mock_client_cls, monkeypatch):
    from app.services.nexus_artifact import fetch_jar_bytes_from_nexus

    monkeypatch.setattr("app.services.nexus_artifact.settings.GIDO_NEXUS_USERNAME", None)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.iter_bytes.return_value = [b"PK\x03\x04" + b"x" * 8]

    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_resp)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    mock_http = MagicMock()
    mock_http.stream.return_value = mock_ctx
    mock_http.__enter__ = MagicMock(return_value=mock_http)
    mock_http.__exit__ = MagicMock(return_value=False)
    mock_client_cls.return_value = mock_http

    content = fetch_jar_bytes_from_nexus("https://nexus.example.com/app.jar")
    assert content.startswith(b"PK\x03\x04")
    _, kwargs = mock_http.stream.call_args
    assert kwargs.get("auth") is None

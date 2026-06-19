# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""S3 认证：IRSA 与 static AK/SK。"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import s3_auth


@pytest.fixture(autouse=True)
def _clear_s3_env(monkeypatch):
    for key in (
        "GIDO_S3_ACCESS_KEY_ID",
        "GIDO_S3_SECRET_ACCESS_KEY",
        "GIDO_S3_SESSION_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolved_mode_irsa_default(monkeypatch):
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://bucket/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "irsa")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True)
    assert s3_auth.resolved_s3_auth_mode() == "irsa"


def test_resolved_mode_static_explicit(monkeypatch):
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://bucket/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "static")
    monkeypatch.setattr(settings, "GIDO_S3_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setattr(settings, "GIDO_S3_SECRET_ACCESS_KEY", "secret")
    assert s3_auth.resolved_s3_auth_mode() == "static"


def test_apply_flink_conf_irsa_provider(monkeypatch):
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "irsa")
    conf: dict = {}
    s3_auth.apply_flink_s3_flink_conf(conf)
    assert conf["fs.s3a.aws.credentials.provider"] == (
        "com.amazonaws.auth.WebIdentityTokenCredentialsProvider"
    )


def test_apply_flink_conf_static_provider(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_CHECKPOINT_DIR", "s3a://b/ckpt")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "static")
    monkeypatch.setattr(settings, "GIDO_S3_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setattr(settings, "GIDO_S3_SECRET_ACCESS_KEY", "secret")
    conf: dict = {}
    s3_auth.apply_flink_s3_flink_conf(conf)
    assert conf["fs.s3a.aws.credentials.provider"] == (
        "com.amazonaws.auth.EnvironmentVariableCredentialsProvider"
    )


def test_flink_pod_template_injects_aws_env(monkeypatch):
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "static")
    monkeypatch.setattr(settings, "GIDO_S3_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setattr(settings, "GIDO_S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_REGION", "ap-northeast-1")
    tpl = s3_auth.operator_s3_credentials_pod_template()
    assert tpl is not None
    env = {e["name"]: e["value"] for e in tpl["spec"]["containers"][0]["env"]}
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA_TEST"
    assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert env["AWS_DEFAULT_REGION"] == "ap-northeast-1"


def test_validate_static_without_keys_fails(monkeypatch):
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "static")
    ok, msg = s3_auth.validate_s3_auth_for_submit()
    assert not ok
    assert "GIDO_S3_ACCESS_KEY_ID" in msg


def test_boto3_kwargs_static(monkeypatch):
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://b/artifacts")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "static")
    monkeypatch.setattr(settings, "GIDO_S3_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setattr(settings, "GIDO_S3_SECRET_ACCESS_KEY", "secret")
    kwargs = s3_auth.boto3_client_kwargs()
    assert kwargs["aws_access_key_id"] == "AKIA_TEST"
    assert kwargs["aws_secret_access_key"] == "secret"


def test_legacy_use_irsa_false_maps_to_static(monkeypatch):
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "irsa")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", False)
    assert s3_auth.resolved_s3_auth_mode() == "static"


def test_profile_static_credentials_override_platform(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "irsa")
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    ctx = OperatorRuntimeContext(
        profile_id=2,
        profile_name="eks-b",
        namespace="flink",
        image="img:1",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081",
        cluster_domain="cluster.local",
        checkpoint_dir="s3a://bucket-b/ckpt",
        image_pull_secrets=None,
        s3_auth_mode="static",
        s3_access_key_id="AKIA_PROFILE",
        s3_secret_access_key="profile-secret",
        s3_session_token=None,
        jar_s3_prefix=None,
        s3_region=None,
        s3_endpoint_url=None,
    )
    snap = s3_auth.build_s3_auth_snapshot(ctx)
    assert snap.source == "profile"
    assert snap.auth_mode == "static"
    assert snap.access_key_id == "AKIA_PROFILE"
    ok, _ = s3_auth.validate_s3_auth_for_submit(ctx)
    assert ok
    tpl = s3_auth.operator_s3_credentials_pod_template(ctx)
    assert tpl is not None
    env = {e["name"]: e["value"] for e in tpl["spec"]["containers"][0]["env"]}
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA_PROFILE"


def test_profile_s3_region_overrides_platform(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_REGION", "us-east-1")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://b/jars")
    ctx = OperatorRuntimeContext(
        profile_id=4,
        profile_name="sg",
        namespace="flink",
        image="img:1",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://x",
        cluster_domain="cluster.local",
        checkpoint_dir=None,
        image_pull_secrets=None,
        s3_auth_mode="static",
        s3_access_key_id="AKIA",
        s3_secret_access_key="secret",
        s3_session_token=None,
        jar_s3_prefix=None,
        s3_region="ap-southeast-1",
        s3_endpoint_url=None,
    )
    kwargs = s3_auth.boto3_client_kwargs(ctx)
    assert kwargs["region_name"] == "ap-southeast-1"


def test_profile_s3_endpoint_overrides_platform(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", "https://s3.us-east-1.amazonaws.com")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://b/jars")
    ctx = OperatorRuntimeContext(
        profile_id=6,
        profile_name="sg",
        namespace="flink",
        image="img:1",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://x",
        cluster_domain="cluster.local",
        checkpoint_dir="s3://b/ckpt",
        image_pull_secrets=None,
        s3_auth_mode="irsa",
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_session_token=None,
        jar_s3_prefix=None,
        s3_region=None,
        s3_endpoint_url="https://s3.ap-southeast-1.amazonaws.com",
    )
    kwargs = s3_auth.boto3_client_kwargs(ctx)
    assert kwargs["endpoint_url"] == "https://s3.ap-southeast-1.amazonaws.com"
    conf: dict = {}
    s3_auth.apply_flink_s3_flink_conf(conf, ctx)
    assert conf["fs.s3a.endpoint"] == "https://s3.ap-southeast-1.amazonaws.com"


def test_profile_irsa_injects_region_env(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_AUTH_MODE", "irsa")
    monkeypatch.setattr(settings, "GIDO_ARTIFACT_S3_REGION", "us-east-1")
    ctx = OperatorRuntimeContext(
        profile_id=5,
        profile_name="sg",
        namespace="flink",
        image="img:1",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://x",
        cluster_domain="cluster.local",
        checkpoint_dir="s3://b/ckpt",
        image_pull_secrets=None,
        s3_auth_mode="irsa",
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_session_token=None,
        jar_s3_prefix=None,
        s3_region="ap-southeast-1",
        s3_endpoint_url=None,
    )
    tpl = s3_auth.operator_s3_credentials_pod_template(ctx)
    assert tpl is not None
    env = {e["name"]: e["value"] for e in tpl["spec"]["containers"][0]["env"]}
    assert env["AWS_DEFAULT_REGION"] == "ap-southeast-1"
    conf: dict = {}
    s3_auth.apply_flink_s3_flink_conf(conf, ctx)
    assert conf["fs.s3a.endpoint.region"] == "ap-southeast-1"


def test_profile_static_missing_keys_fails(monkeypatch):
    from app.services.operator_runtime import OperatorRuntimeContext

    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://b/wh")
    ctx = OperatorRuntimeContext(
        profile_id=3,
        profile_name="bad",
        namespace="flink",
        image="img:1",
        flink_version="v1_17",
        service_account="flink",
        k8s_context=None,
        kubeconfig_path=None,
        jm_rest_template="http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081",
        cluster_domain="cluster.local",
        checkpoint_dir="s3a://bucket/ckpt",
        image_pull_secrets=None,
        s3_auth_mode="static",
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_session_token=None,
        jar_s3_prefix=None,
        s3_region=None,
        s3_endpoint_url=None,
    )
    ok, msg = s3_auth.validate_s3_auth_for_submit(ctx)
    assert not ok
    assert "Operator 集群" in msg

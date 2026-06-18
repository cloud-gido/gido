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

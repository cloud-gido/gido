# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.operator_runtime import OperatorRuntimeContext, _job_runtime_overrides


def test_operator_runtime_context_from_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_NAMESPACE", "flink")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_IMAGE", "img:a")
    ctx = OperatorRuntimeContext.from_settings()
    assert ctx.namespace == "flink"
    assert ctx.image == "img:a"


def test_job_runtime_overrides():
    ov = _job_runtime_overrides(
        {"operator_runtime_image": "ghcr.io/x/y:1", "operator_flink_version": "v2_2", "resource_tier": "small"}
    )
    assert ov["image"] == "ghcr.io/x/y:1"
    assert ov["flink_version"] == "v2_2"


def test_operator_runtime_with_overrides():
    base = OperatorRuntimeContext(
        profile_id=1,
        profile_name="prod",
        namespace="flink-a",
        image="img:default",
        flink_version="v2_2",
        service_account="flink",
        k8s_context="ctx-a",
        kubeconfig_path=None,
        jm_rest_template="http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081",
        cluster_domain="cluster.local",
        checkpoint_dir=None,
        image_pull_secrets="ghcr-pull",
        s3_auth_mode=None,
        s3_access_key_id=None,
        s3_secret_access_key=None,
        s3_session_token=None,
        jar_s3_prefix=None,
        s3_region=None,
        s3_endpoint_url=None,
    )
    merged = base.with_overrides(image="img:override", namespace="flink-b")
    assert merged.image == "img:override"
    assert merged.namespace == "flink-b"
    assert merged.k8s_context == "ctx-a"

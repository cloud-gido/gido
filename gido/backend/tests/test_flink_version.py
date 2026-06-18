# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.flink_version import (
    display_flink_version_from_runtime,
    infer_operator_flink_version_from_image,
    normalize_operator_flink_version,
    operator_flink_version_label,
    operator_version_major_line,
    session_image_major_line,
    supported_operator_flink_versions_public,
)


def test_infer_v1_17_from_image():
    assert infer_operator_flink_version_from_image("apache/flink:1.17.2-java11") == "v1_17"
    assert infer_operator_flink_version_from_image("registry.example/flink-runtime:1.17.2") == "v1_17"


def test_infer_v2_2_from_image():
    assert infer_operator_flink_version_from_image("apache/flink:2.2.1-java11") == "v2_2"
    assert infer_operator_flink_version_from_image("gido-flink-runtime:2.2.1") == "v2_2"


def test_normalize_operator_flink_version():
    assert normalize_operator_flink_version("1.17.2") == "v1_17"
    assert normalize_operator_flink_version("v1_17") == "v1_17"


def test_display_flink_version_from_image_tag():
    assert display_flink_version_from_runtime("v1_17", "apache/flink:1.17.2-java11") == "1.17.2"
    assert display_flink_version_from_runtime("v2_2", "apache/flink:2.2.1-java11") == "2.2.1"


def test_operator_version_label():
    assert "1.17" in operator_flink_version_label("v1_17")


def test_session_and_operator_major_line():
    assert session_image_major_line("apache/flink:1.17.2-java11") == "1"
    assert session_image_major_line("apache/flink:2.2.1-java11") == "2"
    assert operator_version_major_line("v1_17") == "1"
    assert operator_version_major_line("v2_2") == "2"


def test_supported_versions_include_v1_17():
    values = [x["value"] for x in supported_operator_flink_versions_public()]
    assert "v1_17" in values


def test_resolve_operator_runtime_infers_from_profile_image(monkeypatch):
    """Profile 仅指定 1.17.2 镜像、未设 flink_version 时应推断 v1_17。"""
    from app.core.config import settings
    from app.services.operator_runtime import resolve_operator_runtime

    class _Prof:
        id = 9
        name = "legacy"
        flink_operator_namespace = "flink"
        flink_operator_image = "apache/flink:1.17.2-java11"
        flink_operator_flink_version = None
        flink_operator_service_account = None
        flink_k8s_context = None
        flink_k8s_kubeconfig_path = None
        flink_operator_jm_rest_template = None
        flink_k8s_cluster_domain = None
        flink_operator_checkpoint_dir = None
        flink_operator_image_pull_secrets = None

    monkeypatch.setattr(settings, "FLINK_OPERATOR_FLINK_VERSION", "v2_2")

    class _Q:
        def filter(self, *a, **k):
            return self

        def first(self):
            return _Prof()

    class _DB:
        def query(self, *a):
            return _Q()

    ctx = resolve_operator_runtime(_DB(), 1, profile_id=9)
    assert ctx.flink_version == "v1_17"
    assert "1.17.2" in ctx.image

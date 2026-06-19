# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.operator_profile_runtime import (
    flink_version_for_profile_image,
    normalize_runtime_images_payload,
    profile_runtime_images_public,
)


class _Prof:
    flink_operator_image = "gido-flink-runtime:2.2.1"
    flink_operator_flink_version = "v2_2"
    flink_operator_runtime_images = [
        {"label": "Flink 2.2.1", "image": "gido-flink-runtime:2.2.1", "flink_version": "v2_2", "is_default": True},
        {"label": "Flink 1.17.2", "image": "gido-flink-runtime:1.17.2", "flink_version": "v1_17"},
    ]


def test_normalize_runtime_images_from_legacy():
    items = normalize_runtime_images_payload(None, legacy_image="apache/flink:1.17.2-java11", legacy_flink_version="v1_17")
    assert len(items) == 1
    assert items[0]["image"] == "apache/flink:1.17.2-java11"
    assert items[0]["is_default"] is True


def test_normalize_runtime_images_dedupe_and_default():
    items = normalize_runtime_images_payload(
        [
            {"image": "img:a:2.2.1", "flink_version": "v2_2"},
            {"image": "img:a:1.17.2", "flink_version": "v1_17", "is_default": True},
        ]
    )
    assert items[1]["is_default"] is True
    assert not items[0]["is_default"]


def test_flink_version_for_profile_image():
    assert flink_version_for_profile_image(_Prof(), "gido-flink-runtime:1.17.2") == "v1_17"
    assert flink_version_for_profile_image(_Prof(), "unknown:1.0") is None


def test_profile_runtime_images_public():
    pub = profile_runtime_images_public(_Prof())
    assert len(pub) == 2
    assert pub[0]["is_default"] is True

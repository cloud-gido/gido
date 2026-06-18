# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Flink 运行时镜像与 Operator CRD flinkVersion（v1_17 / v2_2 等）映射。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Operator 1.15+ 常见 flinkVersion；与 Flink Kubernetes Operator CRD 对齐
SUPPORTED_OPERATOR_FLINK_VERSIONS: Tuple[str, ...] = (
    "v2_2",
    "v2_0",
    "v1_20",
    "v1_19",
    "v1_18",
    "v1_17",
    "v1_16",
    "v1_15",
)

_OPERATOR_VERSION_LABELS: Dict[str, str] = {
    "v2_2": "Flink 2.2.x",
    "v2_0": "Flink 2.0.x",
    "v1_20": "Flink 1.20.x",
    "v1_19": "Flink 1.19.x",
    "v1_18": "Flink 1.18.x",
    "v1_17": "Flink 1.17.x",
    "v1_16": "Flink 1.16.x",
    "v1_15": "Flink 1.15.x",
}

# 镜像 tag 中 Flink 次版本 → Operator flinkVersion
_IMAGE_MINOR_TO_OPERATOR: Dict[Tuple[int, int], str] = {
    (2, 2): "v2_2",
    (2, 0): "v2_0",
    (1, 20): "v1_20",
    (1, 19): "v1_19",
    (1, 18): "v1_18",
    (1, 17): "v1_17",
    (1, 16): "v1_16",
    (1, 15): "v1_15",
}


def normalize_operator_flink_version(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower().replace("-", "_")
    if not s:
        return None
    if s.startswith("v") and "_" in s:
        return s
    # 容错：1.17 / 1.17.2 → v1_17
    m = re.match(r"^(\d+)\.(\d+)(?:\.\d+)?$", s)
    if m:
        return f"v{int(m.group(1))}_{int(m.group(2))}"
    return s


def infer_operator_flink_version_from_image(image: Optional[str]) -> Optional[str]:
    """从运行时镜像 tag 推断 Operator spec.flinkVersion，如 apache/flink:1.17.2-java11 → v1_17。"""
    if not image or not str(image).strip():
        return None
    text = str(image).strip().lower()
    # 优先匹配 tag 段 :1.17.2-java11 / :1.17.2
    tag = text.rsplit(":", 1)[-1] if ":" in text else text
    m = re.search(r"(?<![\d.])(\d+)\.(\d+)(?:\.(\d+))?(?:[-_]|$|java)", tag)
    if not m:
        m = re.search(r"flink[:\-/]?(\d+)\.(\d+)", text)
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if major == 2 and minor >= 2:
        return "v2_2"
    return _IMAGE_MINOR_TO_OPERATOR.get((major, minor))


def operator_flink_version_label(flink_version: Optional[str]) -> str:
    v = normalize_operator_flink_version(flink_version) or ""
    return _OPERATOR_VERSION_LABELS.get(v, v or "未知")


def display_flink_version_from_runtime(
    operator_flink_version: Optional[str],
    runtime_image: Optional[str] = None,
) -> str:
    """UI 展示用 Flink 版本字符串（如 1.17.2 / 2.2.1）。"""
    if runtime_image:
        tag = str(runtime_image).strip().lower().rsplit(":", 1)[-1]
        m = re.search(r"(\d+\.\d+\.\d+)", tag)
        if m:
            return m.group(1)
        m2 = re.search(r"(\d+\.\d+)", tag)
        if m2:
            return m2.group(1)
    v = normalize_operator_flink_version(operator_flink_version)
    if v and v.startswith("v"):
        parts = v[1:].split("_", 1)
        if len(parts) == 2:
            return f"{parts[0]}.{parts[1]}.x"
    return "2.2.1"


def supported_operator_flink_versions_public() -> List[Dict[str, Any]]:
    return [
        {"value": v, "label": _OPERATOR_VERSION_LABELS.get(v, v)}
        for v in SUPPORTED_OPERATOR_FLINK_VERSIONS
    ]


def session_image_major_line(image: Optional[str]) -> Optional[str]:
    """Session/Application 镜像主版本线：'1' 或 '2'。"""
    if not image:
        return None
    text = str(image).lower()
    if re.search(r"flink:?2\.|[/:]2\.\d", text):
        return "2"
    if re.search(r"flink:?1\.|[/:]1\.\d", text):
        return "1"
    return None


def operator_version_major_line(operator_flink_version: Optional[str]) -> Optional[str]:
    v = normalize_operator_flink_version(operator_flink_version) or ""
    if v.startswith("v2_"):
        return "2"
    if v.startswith("v1_"):
        return "1"
    return None

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Operator Profile 多运行时镜像：存储、校验与作业下拉选项。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from app.services.flink_version import infer_operator_flink_version_from_image, normalize_operator_flink_version

if TYPE_CHECKING:
    from app.models.workspace import FlinkOperatorProfile


def _strip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def normalize_runtime_images_payload(
    raw: Any,
    *,
    legacy_image: Optional[str] = None,
    legacy_flink_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    规范化 Profile 运行时镜像列表。
    每项：label（可选）、image（必填）、flink_version（可推断）、is_default（至多一项为 true）。
    """
    items: List[Dict[str, Any]] = []
    if raw is not None:
        if not isinstance(raw, list):
            raise ValueError("flink_operator_runtime_images 须为数组")
        for idx, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ValueError(f"运行时镜像 #{idx + 1} 须为对象")
            image = _strip(entry.get("image"))
            if not image:
                raise ValueError(f"运行时镜像 #{idx + 1} 须填写 image")
            label = _strip(entry.get("label"))
            fv_raw = _strip(entry.get("flink_version"))
            fv = normalize_operator_flink_version(fv_raw) if fv_raw else None
            if not fv:
                fv = infer_operator_flink_version_from_image(image)
            is_default = bool(entry.get("is_default"))
            items.append(
                {
                    "label": label or _default_label_for_image(image, fv),
                    "image": image,
                    "flink_version": fv,
                    "is_default": is_default,
                }
            )

    if not items:
        leg_img = _strip(legacy_image)
        if leg_img:
            leg_fv = normalize_operator_flink_version(legacy_flink_version) if legacy_flink_version else None
            if not leg_fv:
                leg_fv = infer_operator_flink_version_from_image(leg_img)
            items.append(
                {
                    "label": _default_label_for_image(leg_img, leg_fv),
                    "image": leg_img,
                    "flink_version": leg_fv,
                    "is_default": True,
                }
            )

    if not items:
        return []

    defaults = [i for i in items if i.get("is_default")]
    if len(defaults) > 1:
        raise ValueError("运行时镜像列表中只能有一项 is_default=true")
    if not defaults:
        items[0]["is_default"] = True
    else:
        for i in items:
            if not i.get("is_default"):
                continue
            i["is_default"] = i is defaults[0]

    seen_images: set[str] = set()
    for i in items:
        img = i["image"]
        if img in seen_images:
            raise ValueError(f"运行时镜像重复: {img}")
        seen_images.add(img)

    return items


def _default_label_for_image(image: str, flink_version: Optional[str]) -> str:
    if flink_version:
        return flink_version.replace("_", ".")
    inferred = infer_operator_flink_version_from_image(image)
    if inferred:
        return inferred
    tag = image.rsplit(":", 1)[-1] if ":" in image else image
    return tag[:64]


def default_runtime_image_entry(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    for i in items:
        if i.get("is_default"):
            return i
    return items[0]


def profile_runtime_images_public(profile: "FlinkOperatorProfile") -> List[Dict[str, Any]]:
    raw = getattr(profile, "flink_operator_runtime_images", None)
    try:
        items = normalize_runtime_images_payload(
            raw,
            legacy_image=getattr(profile, "flink_operator_image", None),
            legacy_flink_version=getattr(profile, "flink_operator_flink_version", None),
        )
    except ValueError:
        items = normalize_runtime_images_payload(
            None,
            legacy_image=getattr(profile, "flink_operator_image", None),
            legacy_flink_version=getattr(profile, "flink_operator_flink_version", None),
        )
    return [
        {
            "label": i["label"],
            "image": i["image"],
            "flink_version": i.get("flink_version"),
            "is_default": bool(i.get("is_default")),
        }
        for i in items
    ]


def flink_version_for_profile_image(
    profile: Optional["FlinkOperatorProfile"],
    image: Optional[str],
) -> Optional[str]:
    if profile is None or not (image or "").strip():
        return None
    target = str(image).strip()
    for entry in profile_runtime_images_public(profile):
        if entry.get("image") == target and entry.get("flink_version"):
            return str(entry["flink_version"])
    return None


def sync_profile_legacy_image_fields(data: dict) -> dict:
    """将 runtime_images 默认项同步到 flink_operator_image / flink_operator_flink_version。"""
    out = dict(data)
    items = out.get("flink_operator_runtime_images")
    if items is None and "flink_operator_image" not in out:
        return out
    normalized = normalize_runtime_images_payload(
        items,
        legacy_image=out.get("flink_operator_image"),
        legacy_flink_version=out.get("flink_operator_flink_version"),
    )
    out["flink_operator_runtime_images"] = normalized or None
    default = default_runtime_image_entry(normalized)
    if default:
        out["flink_operator_image"] = default["image"]
        out["flink_operator_flink_version"] = default.get("flink_version")
    return out

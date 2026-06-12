# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-12
"""用户头像：内置 preset ID 与本地文件上传。"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional, Tuple

from app.core.config import settings

AVATAR_PRESET_PREFIX = "preset:"
AVATAR_UPLOAD_PREFIX = "upload:"
VALID_PRESET_IDS = frozenset({"1", "2", "3", "4", "5", "6", "7", "8"})
STORED_NAME_RE = re.compile(r"^[0-9]+_[0-9a-f]{8,}\.(png|jpe?g|webp)$", re.IGNORECASE)

_CONTENT_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}


def avatar_upload_dir() -> Path:
    d = Path(settings.AVATAR_UPLOAD_DIR).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_valid_preset_id(preset_id: str) -> bool:
    return preset_id in VALID_PRESET_IDS


def normalize_avatar_value(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if value.startswith(AVATAR_PRESET_PREFIX):
        preset_id = value[len(AVATAR_PRESET_PREFIX) :]
        if not is_valid_preset_id(preset_id):
            raise ValueError("无效的内置头像")
        return f"{AVATAR_PRESET_PREFIX}{preset_id}"
    if value.startswith(AVATAR_UPLOAD_PREFIX):
        stored = value[len(AVATAR_UPLOAD_PREFIX) :]
        if not STORED_NAME_RE.match(stored):
            raise ValueError("无效的上传头像引用")
        return f"{AVATAR_UPLOAD_PREFIX}{stored}"
    raise ValueError("头像格式无效")


def stored_upload_name(user_id: int, content_type: str) -> str:
    ext = _CONTENT_EXT.get((content_type or "").lower())
    if not ext:
        raise ValueError("仅支持 PNG、JPEG、WebP 图片")
    return f"{int(user_id)}_{uuid.uuid4().hex[:12]}{ext}"


def save_avatar_upload(user_id: int, content: bytes, content_type: str) -> str:
    if len(content) > int(settings.AVATAR_MAX_BYTES):
        raise ValueError(f"图片不能超过 {settings.AVATAR_MAX_BYTES // (1024 * 1024)}MB")
    stored = stored_upload_name(user_id, content_type)
    path = avatar_upload_dir() / stored
    path.write_bytes(content)
    return stored


def avatar_file_path(stored_name: str) -> Optional[Path]:
    if not STORED_NAME_RE.match(stored_name or ""):
        return None
    path = avatar_upload_dir() / stored_name
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def delete_uploaded_avatar(stored_name: str) -> None:
    path = avatar_file_path(stored_name)
    if path and path.is_file():
        path.unlink(missing_ok=True)


def parse_upload_stored_name(avatar: Optional[str]) -> Optional[str]:
    if not avatar or not avatar.startswith(AVATAR_UPLOAD_PREFIX):
        return None
    stored = avatar[len(AVATAR_UPLOAD_PREFIX) :]
    return stored if STORED_NAME_RE.match(stored) else None


def media_type_for_stored_name(stored_name: str) -> str:
    lower = stored_name.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def replace_avatar_upload(user_id: int, old_avatar: Optional[str], content: bytes, content_type: str) -> Tuple[str, str]:
    stored = save_avatar_upload(user_id, content, content_type)
    old_stored = parse_upload_stored_name(old_avatar)
    if old_stored and old_stored != stored:
        delete_uploaded_avatar(old_stored)
    return stored, f"{AVATAR_UPLOAD_PREFIX}{stored}"

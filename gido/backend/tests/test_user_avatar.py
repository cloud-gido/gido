# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import pytest

from app.services.user_avatar import (
    normalize_avatar_value,
    replace_avatar_upload,
    avatar_file_path,
    delete_uploaded_avatar,
)

# 1x1 PNG
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_normalize_preset_avatar():
    assert normalize_avatar_value("preset:3") == "preset:3"
    with pytest.raises(ValueError):
        normalize_avatar_value("preset:99")


def test_upload_and_replace_avatar(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.user_avatar.settings.AVATAR_UPLOAD_DIR", str(tmp_path))
    content = _TINY_PNG

    stored, ref = replace_avatar_upload(1, None, content, "image/png")
    assert ref == f"upload:{stored}"
    assert avatar_file_path(stored) is not None

    stored2, ref2 = replace_avatar_upload(1, ref, content, "image/png")
    assert stored2 != stored
    assert avatar_file_path(stored) is None
    assert avatar_file_path(stored2) is not None
    delete_uploaded_avatar(stored2)
    assert avatar_file_path(stored2) is None

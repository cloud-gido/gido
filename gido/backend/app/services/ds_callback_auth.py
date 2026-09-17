# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Scoped authentication for Dolphin-to-GIDO node callbacks."""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

from app.core.config import settings


def node_callback_signature(node_id: int) -> str:
    key = str(settings.SECRET_KEY or "").encode("utf-8")
    message = f"gido-dolphin-node-callback:v1:{int(node_id)}".encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"v1={digest}"


def verify_node_callback_signature(node_id: int, supplied: Optional[str]) -> bool:
    value = str(supplied or "").strip()
    return bool(value) and hmac.compare_digest(
        value,
        node_callback_signature(node_id),
    )

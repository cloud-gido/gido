# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Ed25519 验签 Cloud GIDO 签发的 signed_license。"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    _HAS_CRYPTO = True
except ImportError:  # pragma: no cover
    Ed25519PublicKey = object  # type: ignore
    load_pem_public_key = None  # type: ignore
    _HAS_CRYPTO = False
    _log.warning("cryptography not installed; license signature verify disabled")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def decode_signed_license(encoded: str) -> Optional[dict[str, Any]]:
    try:
        raw = _b64url_decode(encoded)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        _log.debug("decode signed_license failed", exc_info=True)
        return None


def load_public_key_pem(pem: str) -> Optional[Any]:
    if not _HAS_CRYPTO or load_pem_public_key is None:
        return None
    text = pem.replace("\\n", "\n").strip()
    try:
        key = load_pem_public_key(text.encode("utf-8"))
    except Exception:
        _log.warning("invalid LICENSE_PUBLIC_KEY PEM")
        return None
    if not isinstance(key, Ed25519PublicKey):
        _log.warning("LICENSE_PUBLIC_KEY is not Ed25519")
        return None
    return key


def verify_signed_license(blob: dict[str, Any], public_key: Any) -> bool:
    if not _HAS_CRYPTO:
        return False
    payload = blob.get("payload")
    signature = blob.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return False
    if signature.startswith("dev-unsigned:"):
        return False
    try:
        message = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        public_key.verify(_b64url_decode(signature), message)
        return True
    except Exception:
        _log.debug("license signature verify failed", exc_info=True)
        return False

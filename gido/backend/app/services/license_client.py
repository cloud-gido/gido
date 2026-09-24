# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""调用 Cloud GIDO Fleet license activate / heartbeat / public-key。"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.core.config import settings

_log = logging.getLogger(__name__)


def _base_url() -> str:
    return (settings.LICENSE_SERVER or "https://cloud-gido.com").rstrip("/")


def fetch_public_key_pem(timeout: float = 15.0) -> Optional[str]:
    url = f"{_base_url()}/api/license/public-key"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            if r.status_code != 200:
                _log.warning("public-key HTTP %s", r.status_code)
                return None
            data = r.json()
            return data.get("public_key_pem")
    except Exception:
        _log.warning("fetch public-key failed", exc_info=True)
        return None


def activate(
    deployment_id: str,
    license_key: str,
    fingerprint: Optional[str] = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "deployment_id": deployment_id,
        "license_key": license_key,
    }
    if fingerprint:
        body["instance_fingerprint"] = fingerprint
    url = f"{_base_url()}/api/license/activate"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=body)
        data = r.json() if r.content else {}
        data["_http_status"] = r.status_code
        return data


def heartbeat(
    deployment_id: str,
    license_key: str,
    fingerprint: Optional[str] = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "deployment_id": deployment_id,
        "license_key": license_key,
    }
    if fingerprint:
        body["instance_fingerprint"] = fingerprint
    url = f"{_base_url()}/api/license/heartbeat"
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, json=body)
        data = r.json() if r.content else {}
        data["_http_status"] = r.status_code
        return data

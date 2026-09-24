# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""License 状态缓存与 effective plan 解析。"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings
from app.services import license_client
from app.services.license_crypto import (
    decode_signed_license,
    load_public_key_pem,
    verify_signed_license,
)

_log = logging.getLogger(__name__)
_lock = threading.RLock()

ENTERPRISE_FEATURES: dict[str, Any] = {
    "multi_workspace": True,
    "multi_tenant": True,
    "rbac_advanced": True,
    "sso": True,
    "audit_log": True,
    "max_users": 500,
    "max_workspaces": 100,
}

INVALID_FEATURES: dict[str, Any] = {
    "multi_workspace": False,
    "multi_tenant": False,
    "rbac_advanced": False,
    "sso": False,
    "audit_log": False,
    "max_users": 0,
    "max_workspaces": 0,
}

STANDARD_FEATURES: dict[str, Any] = {
    "multi_workspace": False,
    "multi_tenant": False,
    "rbac_advanced": False,
    "sso": False,
    "audit_log": False,
    "max_users": 5,
    "max_workspaces": 1,
}


@dataclass
class LicenseSnapshot:
    plan: str = "enterprise"
    features: dict[str, Any] = field(default_factory=lambda: dict(ENTERPRISE_FEATURES))
    expires_at: Optional[str] = None
    grace_days: int = 7
    in_grace: bool = False
    reason: str = "open_mode"
    mode: str = "open"
    checked_at: Optional[str] = None
    signed_license: Optional[str] = None
    source: str = "open"

    def to_public_dict(self) -> dict[str, Any]:
        days_left = None
        if self.expires_at:
            try:
                exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
                days_left = max(0, int((exp - datetime.now(timezone.utc)).total_seconds() // 86400))
            except Exception:
                days_left = None
        return {
            "plan": self.plan,
            "features": dict(self.features),
            "expires_at": self.expires_at,
            "grace_days": self.grace_days,
            "in_grace": self.in_grace,
            "reason": self.reason,
            "mode": self.mode,
            "checked_at": self.checked_at,
            "days_left": days_left,
            "source": self.source,
        }


_state: LicenseSnapshot = LicenseSnapshot()
_public_key_pem: Optional[str] = None
_stop_event: Optional[threading.Event] = None
_thread: Optional[threading.Thread] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cache_path() -> Path:
    if settings.LICENSE_CACHE_PATH:
        return Path(settings.LICENSE_CACHE_PATH)
    return Path(os.getcwd()) / ".local" / "gido-license-cache.json"


def _fingerprint() -> str:
    if settings.INSTANCE_FINGERPRINT:
        return settings.INSTANCE_FINGERPRINT.strip()
    install = Path(os.getcwd()) / ".local" / "instance-id"
    try:
        install.parent.mkdir(parents=True, exist_ok=True)
        if install.is_file():
            return install.read_text(encoding="utf-8").strip()
        fid = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
        install.write_text(fid, encoding="utf-8")
        return fid
    except Exception:
        return socket.gethostname() or "gido-instance"


def is_open_mode() -> bool:
    mode = (settings.LICENSE_MODE or "open").strip().lower()
    if mode == "open":
        return True
    # commercial 但未配密钥 → 仍按 open，避免误伤本地开发
    if not (settings.LICENSE_KEY and settings.DEPLOYMENT_ID):
        return True
    return False


def get_snapshot() -> LicenseSnapshot:
    with _lock:
        return LicenseSnapshot(
            plan=_state.plan,
            features=dict(_state.features),
            expires_at=_state.expires_at,
            grace_days=_state.grace_days,
            in_grace=_state.in_grace,
            reason=_state.reason,
            mode=_state.mode,
            checked_at=_state.checked_at,
            signed_license=_state.signed_license,
            source=_state.source,
        )


def _set_state(snap: LicenseSnapshot) -> None:
    global _state
    with _lock:
        _state = snap
        _persist(snap)


def _persist(snap: LicenseSnapshot) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(snap), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        _log.debug("persist license cache failed", exc_info=True)


def _load_cache() -> Optional[LicenseSnapshot]:
    try:
        path = _cache_path()
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return LicenseSnapshot(
            plan=str(data.get("plan") or "invalid"),
            features=dict(data.get("features") or INVALID_FEATURES),
            expires_at=data.get("expires_at"),
            grace_days=int(data.get("grace_days") or 7),
            in_grace=bool(data.get("in_grace")),
            reason=str(data.get("reason") or "cache"),
            mode=str(data.get("mode") or "commercial"),
            checked_at=data.get("checked_at"),
            signed_license=data.get("signed_license"),
            source="cache",
        )
    except Exception:
        return None


def _resolve_public_key_pem() -> Optional[str]:
    global _public_key_pem
    if settings.LICENSE_PUBLIC_KEY:
        return settings.LICENSE_PUBLIC_KEY
    if _public_key_pem:
        return _public_key_pem
    pem = license_client.fetch_public_key_pem()
    if pem:
        _public_key_pem = pem
    return pem


def _apply_remote(data: dict[str, Any], source: str) -> LicenseSnapshot:
    plan = str(data.get("plan") or "invalid")
    features = data.get("features")
    if not isinstance(features, dict):
        features = INVALID_FEATURES if plan == "invalid" else STANDARD_FEATURES
    signed = data.get("signed_license")
    pem = _resolve_public_key_pem()
    if signed and pem:
        key = load_public_key_pem(pem)
        blob = decode_signed_license(str(signed))
        if key and blob and not verify_signed_license(blob, key):
            _log.warning("signed_license verify failed → invalid")
            plan = "invalid"
            features = dict(INVALID_FEATURES)
            source = "verify_failed"
    elif signed and not pem:
        _log.warning("no public key; accepting remote plan without verify")

    snap = LicenseSnapshot(
        plan=plan,
        features=dict(features),
        expires_at=data.get("expires_at"),
        grace_days=int(data.get("grace_days") or 7),
        in_grace=bool(data.get("in_grace")),
        reason=str(data.get("reason") or source),
        mode="commercial",
        checked_at=_now_iso(),
        signed_license=str(signed) if signed else None,
        source=source,
    )
    _set_state(snap)
    return snap


def _offline_still_valid(cached: LicenseSnapshot) -> bool:
    if not cached.checked_at:
        return False
    try:
        checked = datetime.fromisoformat(cached.checked_at.replace("Z", "+00:00"))
    except Exception:
        return False
    grace_h = max(0, int(settings.LICENSE_OFFLINE_GRACE_HOURS or 72))
    age_h = (datetime.now(timezone.utc) - checked).total_seconds() / 3600.0
    return age_h <= grace_h


def refresh(force_activate: bool = False) -> LicenseSnapshot:
    if is_open_mode():
        snap = LicenseSnapshot(
            plan="enterprise",
            features=dict(ENTERPRISE_FEATURES),
            reason="open_mode",
            mode="open",
            checked_at=_now_iso(),
            source="open",
        )
        _set_state(snap)
        return snap

    dep = (settings.DEPLOYMENT_ID or "").strip()
    key = (settings.LICENSE_KEY or "").strip()
    fp = _fingerprint()
    try:
        if force_activate:
            data = license_client.activate(dep, key, fp)
        else:
            data = license_client.heartbeat(dep, key, fp)
        status = int(data.get("_http_status") or 0)
        if status >= 400 and not data.get("ok"):
            # 网络可达但凭证错误
            snap = LicenseSnapshot(
                plan="invalid",
                features=dict(INVALID_FEATURES),
                reason=str(data.get("reason") or f"http_{status}"),
                mode="commercial",
                checked_at=_now_iso(),
                source="remote_error",
            )
            _set_state(snap)
            return snap
        return _apply_remote(data, "activate" if force_activate else "heartbeat")
    except Exception as exc:
        _log.warning("license refresh failed: %s", exc)
        cached = _load_cache()
        if cached and _offline_still_valid(cached) and cached.plan != "invalid":
            cached.source = "offline_cache"
            cached.reason = "offline_grace"
            cached.checked_at = cached.checked_at or _now_iso()
            _set_state(cached)
            return cached
        snap = LicenseSnapshot(
            plan="invalid",
            features=dict(INVALID_FEATURES),
            reason="offline_expired",
            mode="commercial",
            checked_at=_now_iso(),
            source="offline_fail",
        )
        _set_state(snap)
        return snap


def start_background_refresh() -> None:
    global _stop_event, _thread
    if is_open_mode():
        refresh()
        return
    refresh(force_activate=True)
    if _thread and _thread.is_alive():
        return
    _stop_event = threading.Event()

    def _loop() -> None:
        hours = float(settings.LICENSE_HEARTBEAT_HOURS or 12)
        interval = max(60.0, hours * 3600.0)
        assert _stop_event is not None
        while not _stop_event.wait(interval):
            try:
                refresh(force_activate=False)
            except Exception:
                _log.exception("license heartbeat loop error")

    _thread = threading.Thread(target=_loop, name="gido-license-heartbeat", daemon=True)
    _thread.start()


def stop_background_refresh() -> None:
    global _stop_event, _thread
    if _stop_event:
        _stop_event.set()
    _thread = None
    _stop_event = None

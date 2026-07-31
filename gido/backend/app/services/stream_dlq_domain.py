# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""DLQ envelope and replay-audit domain helpers.

Plain Flink SQL can route records that it successfully parsed or exposed via
connector metadata. It cannot promise capture of the original raw Kafka bytes;
that capability requires a DataStream/custom deserializer path.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


class RawByteDlqUnsupported(ValueError):
    pass


@dataclass(frozen=True)
class DlqCapability:
    parsed_record_envelope: bool = True
    connector_metadata: bool = True
    raw_bytes: bool = False
    raw_bytes_requirement: str = "DataStream/custom deserializer"


@dataclass(frozen=True)
class DlqEnvelope:
    envelope_version: int
    source: str
    payload: Mapping[str, Any]
    error_code: str
    error_message: str
    failed_at: str
    metadata: Mapping[str, Any]
    raw_bytes_captured: bool = False


@dataclass(frozen=True)
class ReplayAudit:
    replay_id: str
    dlq_record_id: str
    requested_by: str
    requested_at: str
    target: str
    reason: str
    payload_sha256: str


def dlq_capability(*, require_raw_bytes: bool = False) -> DlqCapability:
    capability = DlqCapability()
    if require_raw_bytes:
        raise RawByteDlqUnsupported(
            "plain SQL does not guarantee raw-byte DLQ capture; use a DataStream/custom deserializer"
        )
    return capability


def build_dlq_envelope(
    *,
    source: str,
    payload: Mapping[str, Any],
    error_code: str,
    error_message: str,
    metadata: Optional[Mapping[str, Any]] = None,
    failed_at: Optional[datetime] = None,
) -> DlqEnvelope:
    return DlqEnvelope(
        envelope_version=1,
        source=source,
        payload=dict(payload),
        error_code=error_code,
        error_message=error_message,
        failed_at=(failed_at or datetime.now(timezone.utc)).isoformat(),
        metadata=dict(metadata or {}),
        raw_bytes_captured=False,
    )


def create_replay_audit(
    *,
    dlq_record_id: str,
    envelope: DlqEnvelope,
    requested_by: str,
    target: str,
    reason: str,
    requested_at: Optional[datetime] = None,
) -> ReplayAudit:
    timestamp = (requested_at or datetime.now(timezone.utc)).isoformat()
    canonical = json.dumps(asdict(envelope), sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    replay_seed = f"{dlq_record_id}\0{requested_by}\0{target}\0{timestamp}\0{payload_hash}"
    replay_id = hashlib.sha256(replay_seed.encode("utf-8")).hexdigest()[:32]
    return ReplayAudit(
        replay_id=replay_id,
        dlq_record_id=dlq_record_id,
        requested_by=requested_by,
        requested_at=timestamp,
        target=target,
        reason=reason,
        payload_sha256=payload_hash,
    )

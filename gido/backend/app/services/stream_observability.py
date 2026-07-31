# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-through Stream observability contracts.

Adapters return snapshots only. They intentionally contain no time-series
persistence and fail closed with a redacted ``unavailable`` response.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence, Tuple

import requests


class Availability(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Observation:
    source: str
    status: Availability
    observed_at: datetime
    data: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    retryable: bool = False


@dataclass(frozen=True)
class AlertSummary:
    code: str
    severity: AlertSeverity
    active: bool
    message: str
    observed_value: Optional[float] = None
    threshold: Optional[float] = None
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SLOSummary:
    name: str
    target: float
    actual: Optional[float]
    window: str
    status: Availability
    compliant: Optional[bool]
    error_budget_remaining: Optional[float] = None


class ObservabilityAdapter(Protocol):
    def observe(self) -> Observation: ...


class KafkaOffsetsProvider(Protocol):
    def consumer_group_offsets(self, group_id: str, topic: str) -> Mapping[int, int]: ...

    def topic_end_offsets(self, topic: str) -> Mapping[int, int]: ...


class PaimonCommitProvider(Protocol):
    def latest_commits(self, table_identifier: str, limit: int) -> Sequence[Mapping[str, Any]]: ...


_SENSITIVE_KEYS = {
    "authorization",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "access_key",
    "token",
    "sasl.jaas.config",
}
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_URL_CREDENTIAL_RE = re.compile(r"(https?://)([^/@:\s]+):([^/@\s]+)@")
_KEY_VALUE_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|secret_key|access_key|token)\s*[:=]\s*([^\s,;]+)"
)


def _is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower()
    return normalized in _SENSITIVE_KEYS or any(
        marker in normalized
        for marker in ("password", "passwd", "secret", "access_key", "authorization", "token")
    )


def redact_text(value: Any) -> str:
    """Remove common credential forms from diagnostics before returning/logging."""
    text = str(value)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", text)
    return _KEY_VALUE_SECRET_RE.sub(r"\1=[REDACTED]", text)


def redact_mapping(value: Any) -> Any:
    """Recursively redact secrets while preserving a response's shape."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(key)
                else redact_mapping(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def unavailable_observation(source: str, error: Any, *, retryable: bool = True) -> Observation:
    return Observation(
        source=source,
        status=Availability.UNAVAILABLE,
        observed_at=datetime.now(timezone.utc),
        data={},
        error=redact_text(error),
        retryable=retryable,
    )


class KafkaLagAdapter:
    def __init__(self, provider: KafkaOffsetsProvider):
        self.provider = provider

    def observe(self, *, topic: str, group_id: str) -> Observation:
        try:
            committed = self.provider.consumer_group_offsets(group_id, topic)
            end = self.provider.topic_end_offsets(topic)
            partitions = sorted(set(committed) | set(end))
            rows = []
            for partition in partitions:
                committed_offset = committed.get(partition)
                end_offset = end.get(partition)
                lag = (
                    max(0, int(end_offset) - int(committed_offset))
                    if committed_offset is not None and end_offset is not None
                    else None
                )
                rows.append(
                    {
                        "partition": int(partition),
                        "committed_offset": committed_offset,
                        "end_offset": end_offset,
                        "lag": lag,
                    }
                )
            known_lags = [row["lag"] for row in rows if row["lag"] is not None]
            status = Availability.AVAILABLE if len(known_lags) == len(rows) else Availability.PARTIAL
            unknown_partitions = [
                row["partition"] for row in rows if row["lag"] is None
            ]
            return Observation(
                source="kafka",
                status=status,
                observed_at=datetime.now(timezone.utc),
                data={
                    "topic": topic,
                    "group_id": group_id,
                    "partitions": rows,
                    "total_lag": (
                        sum(known_lags)
                        if status == Availability.AVAILABLE
                        else None
                    ),
                    "known_total_lag": sum(known_lags),
                    "unknown_partitions": unknown_partitions,
                },
            )
        except Exception as exc:
            return unavailable_observation("kafka", exc)


class KafkaPythonOffsetsProvider:
    """Read Kafka committed/end offsets without mutating the consumer group."""

    def __init__(self, *, bootstrap_servers: str, properties: Optional[Mapping[str, str]] = None):
        self.bootstrap_servers = bootstrap_servers
        self.properties = dict(properties or {})

    def _consumer(self, group_id: str):
        from kafka import KafkaConsumer

        kwargs: Dict[str, Any] = {
            "bootstrap_servers": self.bootstrap_servers,
            "group_id": group_id,
            "enable_auto_commit": False,
            "consumer_timeout_ms": 5000,
            "request_timeout_ms": 5000,
            "api_version_auto_timeout_ms": 5000,
        }
        option_map = {
            "properties.security.protocol": "security_protocol",
            "properties.sasl.mechanism": "sasl_mechanism",
            "properties.sasl.username": "sasl_plain_username",
            "properties.sasl.password": "sasl_plain_password",
            "properties.ssl.cafile": "ssl_cafile",
            "properties.ssl.certfile": "ssl_certfile",
            "properties.ssl.keyfile": "ssl_keyfile",
        }
        for source_key, target_key in option_map.items():
            value = self.properties.get(source_key)
            if value not in (None, ""):
                kwargs[target_key] = value
        jaas = str(self.properties.get("properties.sasl.jaas.config") or "")
        if jaas:
            username = re.search(r'\busername\s*=\s*"([^"]*)"', jaas)
            password = re.search(r'\bpassword\s*=\s*"([^"]*)"', jaas)
            if username:
                kwargs["sasl_plain_username"] = username.group(1)
            if password:
                kwargs["sasl_plain_password"] = password.group(1)
        return KafkaConsumer(**kwargs)

    def _topic_partitions(self, consumer, topic: str):
        from kafka import TopicPartition

        partitions = consumer.partitions_for_topic(topic)
        if partitions is None:
            raise RuntimeError(f"Kafka topic is unavailable: {topic}")
        return [TopicPartition(topic, int(partition)) for partition in sorted(partitions)]

    def discover_topics(self) -> Sequence[Mapping[str, Any]]:
        consumer = self._consumer("gido-pipeline-discovery")
        try:
            topics = sorted(consumer.topics())
            return [
                {
                    "name": topic,
                    "partitions": len(consumer.partitions_for_topic(topic) or ()),
                }
                for topic in topics
            ]
        finally:
            consumer.close()

    def consumer_group_offsets(self, group_id: str, topic: str) -> Mapping[int, int]:
        consumer = self._consumer(group_id)
        try:
            result: Dict[int, int] = {}
            for topic_partition in self._topic_partitions(consumer, topic):
                offset = consumer.committed(topic_partition)
                if offset is not None:
                    result[int(topic_partition.partition)] = int(offset)
            return result
        finally:
            consumer.close()

    def topic_end_offsets(self, topic: str) -> Mapping[int, int]:
        # A temporary observer group avoids modifying the pipeline's group offsets.
        observer_group = f"gido-observer-{abs(hash((self.bootstrap_servers, topic))) % 10_000_000}"
        consumer = self._consumer(observer_group)
        try:
            partitions = self._topic_partitions(consumer, topic)
            return {
                int(topic_partition.partition): int(offset)
                for topic_partition, offset in consumer.end_offsets(partitions).items()
            }
        finally:
            consumer.close()


class FlinkRestObservabilityAdapter:
    """Fetch current Flink metrics through an injectable JSON transport."""

    METRIC_IDS = (
        "numRecordsInPerSecond",
        "numRecordsOutPerSecond",
        "backPressuredTimeMsPerSecond",
        "numRestarts",
    )

    def __init__(
        self,
        base_url: str,
        *,
        get_json: Optional[Callable[[str, Optional[Mapping[str, str]]], Any]] = None,
        timeout_seconds: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._get_json = get_json or self._requests_get_json

    def _requests_get_json(self, url: str, params: Optional[Mapping[str, str]]) -> Any:
        response = requests.get(url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _metric_map(payload: Any) -> Dict[str, Optional[float]]:
        values: Dict[str, Optional[float]] = {}
        for row in payload if isinstance(payload, list) else []:
            metric_id = str(row.get("id") or "")
            raw = row.get("value")
            try:
                values[metric_id] = float(raw)
            except (TypeError, ValueError):
                values[metric_id] = None
        return values

    def observe(self, *, job_id: str) -> Observation:
        if not self.base_url:
            return unavailable_observation("flink", "Flink REST URL is not configured", retryable=False)
        try:
            metrics_payload = self._get_json(
                f"{self.base_url}/jobs/{job_id}/metrics",
                {"get": ",".join(self.METRIC_IDS)},
            )
            checkpoints = self._get_json(f"{self.base_url}/jobs/{job_id}/checkpoints", None)
            metrics = self._metric_map(metrics_payload)
            counts = checkpoints.get("counts") if isinstance(checkpoints, Mapping) else {}
            latest = checkpoints.get("latest") if isinstance(checkpoints, Mapping) else {}
            checkpoint = latest.get("completed") if isinstance(latest, Mapping) else None
            status = (
                Availability.AVAILABLE
                if all(metrics.get(metric_id) is not None for metric_id in self.METRIC_IDS)
                and isinstance(checkpoints, Mapping)
                else Availability.PARTIAL
            )
            return Observation(
                source="flink",
                status=status,
                observed_at=datetime.now(timezone.utc),
                data={
                    "job_id": job_id,
                    "throughput": {
                        "records_in_per_second": metrics.get("numRecordsInPerSecond"),
                        "records_out_per_second": metrics.get("numRecordsOutPerSecond"),
                    },
                    "backpressure_ms_per_second": metrics.get("backPressuredTimeMsPerSecond"),
                    "restart_count": metrics.get("numRestarts"),
                    "checkpoint": {
                        "counts": counts or {},
                        "latest_completed": checkpoint,
                    },
                },
            )
        except Exception as exc:
            return unavailable_observation("flink", exc)


class PrometheusQueryAdapter:
    """Read named instant queries; callers own query templates and labels."""

    def __init__(
        self,
        base_url: str,
        *,
        get_json: Optional[Callable[[str, Optional[Mapping[str, str]]], Any]] = None,
        timeout_seconds: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._get_json = get_json or self._requests_get_json

    def _requests_get_json(self, url: str, params: Optional[Mapping[str, str]]) -> Any:
        response = requests.get(url, params=params, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def observe(self, *, queries: Mapping[str, str]) -> Observation:
        if not self.base_url:
            return unavailable_observation(
                "prometheus", "Prometheus URL is not configured", retryable=False
            )
        try:
            values: Dict[str, Any] = {}
            for name, query in sorted(queries.items()):
                payload = self._get_json(
                    f"{self.base_url}/api/v1/query", {"query": query}
                )
                if not isinstance(payload, Mapping) or payload.get("status") != "success":
                    raise RuntimeError(f"Prometheus query failed: {name}")
                values[name] = redact_mapping(
                    ((payload.get("data") or {}).get("result") or [])
                )
            return Observation(
                source="prometheus",
                status=Availability.AVAILABLE,
                observed_at=datetime.now(timezone.utc),
                data={"queries": values},
            )
        except Exception as exc:
            return unavailable_observation("prometheus", exc)


class PaimonCommitAdapter:
    def __init__(self, provider: PaimonCommitProvider):
        self.provider = provider

    def observe(self, *, table_identifier: str, limit: int = 20) -> Observation:
        safe_limit = min(100, max(1, int(limit)))
        try:
            commits = self.provider.latest_commits(table_identifier, safe_limit)
            return Observation(
                source="paimon",
                status=Availability.AVAILABLE,
                observed_at=datetime.now(timezone.utc),
                data={
                    "table_identifier": table_identifier,
                    "commits": redact_mapping(list(commits)),
                    "count": len(commits),
                },
            )
        except Exception as exc:
            return unavailable_observation("paimon", exc)


class S3PaimonCommitProvider:
    """Read Paimon snapshot metadata directly from an S3 filesystem metastore."""

    def __init__(self, warehouse: str):
        from urllib.parse import urlparse

        parsed = urlparse(str(warehouse or "").strip())
        if parsed.scheme not in ("s3", "s3a") or not parsed.netloc:
            raise ValueError("Paimon commit observation requires an s3:// warehouse")
        import boto3
        from app.core.config import settings

        self.bucket = parsed.netloc
        self.root = parsed.path.strip("/")
        self.client = boto3.client(
            "s3",
            region_name=getattr(settings, "GIDO_ARTIFACT_S3_REGION", None),
            endpoint_url=getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None),
        )

    def latest_commits(
        self, table_identifier: str, limit: int
    ) -> Sequence[Mapping[str, Any]]:
        import json

        parts = [part.strip() for part in str(table_identifier).split(".") if part.strip()]
        if len(parts) != 2:
            raise ValueError("Paimon table identifier must be database.table")
        base = "/".join(part for part in (self.root, parts[0], parts[1]) if part)
        prefix = f"{base}/snapshot/"
        objects = []
        token = None
        while True:
            request = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if token:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            objects.extend(
                item
                for item in response.get("Contents", [])
                if str(item.get("Key") or "")
                .rsplit("/", 1)[-1]
                .startswith("snapshot-")
            )
            if not response.get("IsTruncated"):
                break
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError(
                    "S3 snapshot listing was truncated without a continuation token"
                )

        def snapshot_id(item: Mapping[str, Any]) -> int:
            name = str(item.get("Key") or "").rsplit("/", 1)[-1]
            try:
                return int(name.split("-", 1)[1])
            except (ValueError, IndexError):
                return -1

        commits = []
        for item in sorted(objects, key=snapshot_id, reverse=True)[:limit]:
            body = self.client.get_object(
                Bucket=self.bucket, Key=item["Key"]
            )["Body"].read()
            payload = json.loads(body)
            commits.append(
                {
                    "snapshot_id": payload.get("id", snapshot_id(item)),
                    "schema_id": payload.get("schemaId"),
                    "commit_kind": payload.get("commitKind"),
                    "time_millis": payload.get("timeMillis"),
                    "total_record_count": payload.get("totalRecordCount"),
                    "delta_record_count": payload.get("deltaRecordCount"),
                }
            )
        return commits


def summarize_lag_slo(
    observation: Observation,
    *,
    maximum_lag: int,
    window: str = "current",
) -> SLOSummary:
    if observation.status != Availability.AVAILABLE:
        return SLOSummary(
            name="kafka_consumer_lag",
            target=float(maximum_lag),
            actual=None,
            window=window,
            status=observation.status,
            compliant=None,
        )
    actual = float(observation.data.get("total_lag", 0))
    return SLOSummary(
        name="kafka_consumer_lag",
        target=float(maximum_lag),
        actual=actual,
        window=window,
        status=observation.status,
        compliant=actual <= maximum_lag,
        error_budget_remaining=float(maximum_lag) - actual,
    )

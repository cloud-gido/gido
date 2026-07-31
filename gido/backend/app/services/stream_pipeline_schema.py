# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Schema compatibility and deployment preflight rules."""
from __future__ import annotations

import ipaddress
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urlparse

import requests

from app.core.config import settings

from app.services.stream_pipeline_compiler import compile_pipeline
from app.services.stream_pipeline_spec import PipelineSpec, SchemaField, parse_pipeline_spec

_WIDENING = {
    "TINYINT": {"SMALLINT", "INT", "BIGINT"},
    "SMALLINT": {"INT", "BIGINT"},
    "INT": {"BIGINT"},
    "FLOAT": {"DOUBLE"},
}


def discover_schema_registry_subjects(
    registry_url: str,
    *,
    basic_auth: Optional[str] = None,
    timeout_seconds: float = 5.0,
) -> list[str]:
    """Query Confluent-compatible /subjects without following redirects."""
    parsed = urlparse(str(registry_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Schema Registry URL must use http or https")
    if parsed.hostname.lower() in ("localhost", "localhost.localdomain"):
        raise ValueError("Schema Registry URL targets a blocked local address")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    # Hostnames and private service addresses are valid for in-cluster registries.
    if address and (
        address.is_link_local or address.is_loopback or address.is_unspecified
    ):
        raise ValueError("Schema Registry URL targets a blocked local address")
    auth = None
    if basic_auth:
        username, separator, password = str(basic_auth).partition(":")
        if not separator:
            raise ValueError("Schema Registry basic auth secret must be username:password")
        auth = (username, password)
    response = requests.get(
        f"{str(registry_url).rstrip('/')}/subjects",
        auth=auth,
        timeout=timeout_seconds,
        allow_redirects=False,
        headers={"Accept": "application/vnd.schemaregistry.v1+json"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Schema Registry /subjects returned an invalid payload")
    return sorted(str(subject) for subject in payload)


def discover_paimon_tables(options: Mapping[str, Any]) -> list[dict[str, str]]:
    """Discover filesystem-metastore tables from an S3 warehouse using list prefixes."""
    warehouse = str(options.get("warehouse") or "").strip()
    parsed = urlparse(warehouse)
    if parsed.scheme not in ("s3", "s3a") or not parsed.netloc:
        raise ValueError("Paimon discovery currently requires an s3:// warehouse")
    import boto3

    client = boto3.client(
        "s3",
        region_name=getattr(settings, "GIDO_ARTIFACT_S3_REGION", None),
        endpoint_url=getattr(settings, "GIDO_ARTIFACT_S3_ENDPOINT_URL", None),
    )
    root = parsed.path.strip("/")
    root_prefix = f"{root}/" if root else ""

    def prefixes(prefix: str) -> list[dict[str, Any]]:
        token = None
        values: list[dict[str, Any]] = []
        while True:
            request = {
                "Bucket": parsed.netloc,
                "Prefix": prefix,
                "Delimiter": "/",
                "MaxKeys": 1000,
            }
            if token:
                request["ContinuationToken"] = token
            response = client.list_objects_v2(**request)
            values.extend(response.get("CommonPrefixes", []))
            if not response.get("IsTruncated"):
                return values
            token = response.get("NextContinuationToken")
            if not token:
                raise RuntimeError("S3 listing was truncated without a continuation token")

    databases = prefixes(root_prefix)
    tables: list[dict[str, str]] = []
    for database_prefix in databases:
        db_prefix = str(database_prefix.get("Prefix") or "")
        database = db_prefix.rstrip("/").split("/")[-1]
        if not database:
            continue
        children = prefixes(db_prefix)
        for table_prefix in children:
            table = str(table_prefix.get("Prefix") or "").rstrip("/").split("/")[-1]
            if table:
                tables.append(
                    {
                        "database": database,
                        "table": table,
                        "path": f"{warehouse.rstrip('/')}/{database}/{table}",
                    }
                )
    return sorted(tables, key=lambda item: (item["database"], item["table"]))


def _fields(values: Iterable[SchemaField | Mapping[str, Any]]) -> dict[str, SchemaField]:
    return {
        field.name: field
        for field in (
            value if isinstance(value, SchemaField) else SchemaField.model_validate(value)
            for value in values
        )
    }


def diff_schema(
    previous: Iterable[SchemaField | Mapping[str, Any]],
    proposed: Iterable[SchemaField | Mapping[str, Any]],
) -> dict:
    old, new = _fields(previous), _fields(proposed)
    changes = []
    breaking = False
    for name in sorted(old.keys() - new.keys()):
        changes.append({"field": name, "change": "removed", "severity": "breaking"})
        breaking = True
    for name in sorted(new.keys() - old.keys()):
        severity = "compatible" if new[name].nullable else "breaking"
        changes.append({"field": name, "change": "added", "severity": severity})
        breaking = breaking or severity == "breaking"
    for name in sorted(old.keys() & new.keys()):
        before, after = old[name], new[name]
        bt, at = before.data_type.upper(), after.data_type.upper()
        if bt != at:
            severity = "compatible" if at in _WIDENING.get(bt, set()) else "breaking"
            changes.append(
                {"field": name, "change": "type", "from": bt, "to": at, "severity": severity}
            )
            breaking = breaking or severity == "breaking"
        if before.nullable and not after.nullable:
            changes.append({"field": name, "change": "nullable_tightened", "severity": "breaking"})
            breaking = True
    return {"compatible": not breaking, "changes": changes}


def preflight_pipeline(
    spec: PipelineSpec | Mapping[str, Any],
    *,
    previous_schema: Optional[Iterable[SchemaField | Mapping[str, Any]]] = None,
    connection_profiles: Optional[Mapping[int, str]] = None,
) -> dict:
    parsed = parse_pipeline_spec(spec)
    checks = []
    if connection_profiles is not None:
        for label, profile_id, expected in (
            ("source", parsed.source.connection_profile_id, "kafka"),
            ("sink", parsed.sink.connection_profile_id, "paimon"),
        ):
            actual = connection_profiles.get(profile_id)
            checks.append(
                {
                    "check": f"{label}_connection_profile",
                    "status": "passed" if actual == expected else "failed",
                    "detail": None if actual == expected else f"expected {expected}, got {actual or 'missing'}",
                }
            )
    schema_diff = diff_schema(previous_schema, parsed.schema_fields) if previous_schema is not None else None
    if schema_diff is not None:
        policy_allows = schema_diff["compatible"] and (
            parsed.schema_evolution == "additive"
            or not schema_diff["changes"]
        )
        checks.append(
            {
                "check": "schema_compatibility",
                "status": "passed" if policy_allows else "failed",
                "detail": {
                    **schema_diff,
                    "policy": parsed.schema_evolution,
                    "policy_allows": policy_allows,
                },
            }
        )
    try:
        artifact = compile_pipeline(parsed)
        checks.append({"check": "deterministic_compile", "status": "passed", "detail": None})
    except (ValueError, TypeError) as exc:
        artifact = None
        checks.append({"check": "deterministic_compile", "status": "failed", "detail": str(exc)})
    return {
        "ok": all(item["status"] == "passed" for item in checks),
        "checks": checks,
        "schema_diff": schema_diff,
        "compiler_version": artifact["compiler_version"] if artifact else None,
        "spec_hash": artifact["spec_hash"] if artifact else None,
    }

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Resolve pipeline connection profiles only at execution time."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Dict, Mapping

from sqlalchemy.orm import Session

from app.models.workspace import WorkspaceVariable
from app.services.stream_pipeline_compiler import PipelineCompileError, render_pipeline_sql
from app.services.stream_pipeline_spec import PipelineSpec, parse_pipeline_spec


_KAFKA_SECRET_OPTION_PREFIXES = (
    "properties.sasl.",
    "properties.ssl.",
    "schema.registry.",
    "debezium-avro-confluent.",
    "avro-confluent.",
    "protobuf-confluent.",
)
_PAIMON_SECRET_OPTION_PREFIXES = ("catalog.", "fs.")


@dataclass(frozen=True)
class ResolvedPipelineRuntime:
    sql: str
    secret_env: Dict[str, str]


def validate_secret_option_key(connector_type: str, key: str) -> None:
    normalized = str(key or "").strip()
    allowed = (
        _KAFKA_SECRET_OPTION_PREFIXES
        if connector_type == "kafka"
        else _PAIMON_SECRET_OPTION_PREFIXES
    )
    if not normalized or not normalized.startswith(allowed):
        raise PipelineCompileError(
            f"unsupported {connector_type} secret option key: {normalized or '<empty>'}"
        )


def _resolve_secret_refs(
    db: Session,
    *,
    workspace_id: int,
    connector_type: str,
    secret_refs: Mapping[str, str],
) -> Dict[str, str]:
    if not secret_refs:
        return {}
    for key in secret_refs:
        validate_secret_option_key(connector_type, key)
    variable_keys = {str(value).strip() for value in secret_refs.values() if str(value).strip()}
    rows = (
        db.query(WorkspaceVariable)
        .filter(
            WorkspaceVariable.workspace_id == int(workspace_id),
            WorkspaceVariable.var_key.in_(variable_keys),
            WorkspaceVariable.scope.in_(["all", "stream"]),
        )
        .all()
    )
    by_key = {row.var_key: row for row in rows}
    resolved: Dict[str, str] = {}
    for option_key, variable_key_raw in secret_refs.items():
        variable_key = str(variable_key_raw or "").strip()
        row = by_key.get(variable_key)
        if not row or not row.is_secret or row.var_value in (None, ""):
            raise PipelineCompileError(
                f"pipeline secret reference is missing or not marked secret: {variable_key}"
            )
        resolved[str(option_key)] = str(row.var_value)
    return resolved


def resolve_connection_profile_secrets(
    db: Session,
    *,
    workspace_id: int,
    connector_type: str,
    secret_refs: Mapping[str, str],
) -> Dict[str, str]:
    return _resolve_secret_refs(
        db,
        workspace_id=workspace_id,
        connector_type=connector_type,
        secret_refs=secret_refs,
    )


def _secret_placeholders(
    connector_type: str,
    values: Mapping[str, str],
) -> tuple[Dict[str, str], Dict[str, str]]:
    options: Dict[str, str] = {}
    env: Dict[str, str] = {}
    for option_key, value in sorted(values.items()):
        digest = hashlib.sha256(
            f"{connector_type}:{option_key}".encode("utf-8")
        ).hexdigest()[:16].upper()
        env_name = f"GIDO_PIPELINE_SECRET_{digest}"
        options[option_key] = f"${{env:{env_name}}}"
        env[env_name] = value
    return options, env


def resolve_pipeline_runtime(
    db: Session,
    *,
    workspace_id: int,
    spec: PipelineSpec | Mapping,
) -> ResolvedPipelineRuntime:
    """Build placeholder SQL plus Secret-backed environment values."""
    parsed = parse_pipeline_spec(spec)
    # Imported lazily to avoid coupling model registration to service imports.
    from app.api.stream_pipeline import StreamConnectionProfile

    profile_ids = {
        int(parsed.source.connection_profile_id),
        int(parsed.sink.connection_profile_id),
    }
    profiles = (
        db.query(StreamConnectionProfile)
        .filter(
            StreamConnectionProfile.id.in_(profile_ids),
            StreamConnectionProfile.workspace_id == int(workspace_id),
            StreamConnectionProfile.is_active.is_(True),
        )
        .all()
    )
    by_id = {int(profile.id): profile for profile in profiles}
    source = by_id.get(int(parsed.source.connection_profile_id))
    sink = by_id.get(int(parsed.sink.connection_profile_id))
    if not source or source.connector_type != "kafka":
        raise PipelineCompileError("active Kafka connection profile is unavailable")
    if not sink or sink.connector_type != "paimon":
        raise PipelineCompileError("active Paimon connection profile is unavailable")
    allowed_namespaces = {
        item.strip()
        for item in str((sink.options or {}).get("allowed.namespaces") or "").split(",")
        if item.strip()
    }
    if not allowed_namespaces:
        raise PipelineCompileError(
            "Paimon connection profile requires an allowed.namespaces whitelist"
        )
    if parsed.sink.database not in allowed_namespaces:
        raise PipelineCompileError(
            f"Paimon namespace is outside the connection profile whitelist: {parsed.sink.database}"
        )

    all_source_secrets = _resolve_secret_refs(
        db,
        workspace_id=workspace_id,
        connector_type="kafka",
        secret_refs=source.secret_refs or {},
    )
    source_secrets = {
        key: value
        for key, value in all_source_secrets.items()
        if str(key).startswith("properties.")
    }
    sink_secrets = _resolve_secret_refs(
        db,
        workspace_id=workspace_id,
        connector_type="paimon",
        secret_refs=sink.secret_refs or {},
    )
    source_placeholders, source_env = _secret_placeholders(
        "kafka", source_secrets
    )
    sink_placeholders, sink_env = _secret_placeholders("paimon", sink_secrets)
    return ResolvedPipelineRuntime(
        sql=render_pipeline_sql(
            parsed,
            source_profile_options=source.options or {},
            sink_profile_options=sink.options or {},
            source_secret_options=source_placeholders,
            sink_secret_options=sink_placeholders,
        ),
        secret_env={**source_env, **sink_env},
    )


def resolve_pipeline_sql_for_runtime(
    db: Session,
    *,
    workspace_id: int,
    spec: PipelineSpec | Mapping,
) -> str:
    return resolve_pipeline_runtime(
        db, workspace_id=workspace_id, spec=spec
    ).sql

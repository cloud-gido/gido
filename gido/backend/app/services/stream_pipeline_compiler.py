# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic compiler for typed Kafka -> Paimon pipeline specs."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Mapping, Optional

from app.services.stream_pipeline_spec import PipelineSpec, parse_pipeline_spec

COMPILER_VERSION = "gido-stream-pipeline/1.0.0"

KAFKA_OPTION_WHITELIST = frozenset(
    {
        "properties.auto.offset.reset",
        "properties.isolation.level",
        "scan.topic-partition-discovery.interval",
        "json.fail-on-missing-field",
        "json.ignore-parse-errors",
        "debezium-json.schema-include",
    }
)
PAIMON_OPTION_WHITELIST = frozenset(
    {
        "bucket",
        "bucket-key",
        "changelog-producer",
        "file.format",
        "merge-engine",
        "sequence.field",
        "sink.parallelism",
        "target-file-size",
        "write-buffer-size",
    }
)
_SECRET_KEY = re.compile(r"(password|secret|token|credential|api[._-]?key)", re.I)


class PipelineCompileError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _validated_options(options: Mapping[str, str], allowed: frozenset[str], section: str) -> Dict[str, str]:
    rejected = sorted(set(options) - allowed)
    secret_keys = sorted(key for key in options if _SECRET_KEY.search(key))
    if secret_keys:
        raise PipelineCompileError(
            f"{section} options may not contain secrets; use connection_profile.secret_refs: {secret_keys}"
        )
    if rejected:
        raise PipelineCompileError(f"unsupported {section} connector options: {rejected}")
    return {key: str(options[key]) for key in sorted(options)}


def _source_profile_options(options: Mapping[str, Any]) -> Dict[str, str]:
    required = str(options.get("bootstrap.servers") or "").strip()
    if not required:
        raise PipelineCompileError("Kafka connection profile requires bootstrap.servers")
    mapped = {"properties.bootstrap.servers": required}
    for source_key, target_key in (
        ("client.id", "properties.client.id"),
        ("security.protocol", "properties.security.protocol"),
        ("sasl.mechanism", "properties.sasl.mechanism"),
    ):
        value = str(options.get(source_key) or "").strip()
        if value:
            mapped[target_key] = value
    return mapped


def _sink_catalog_options(options: Mapping[str, Any]) -> Dict[str, str]:
    warehouse = str(options.get("warehouse") or "").strip()
    if not warehouse:
        raise PipelineCompileError("Paimon connection profile requires warehouse")
    result = {"type": "paimon", "warehouse": warehouse}
    for key in ("metastore", "uri"):
        value = str(options.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def normalized_spec(spec: PipelineSpec | Mapping[str, Any]) -> dict:
    parsed = parse_pipeline_spec(spec)
    value = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
    value["source"]["options"] = _validated_options(
        parsed.source.options, KAFKA_OPTION_WHITELIST, "kafka"
    )
    value["sink"]["options"] = _validated_options(
        parsed.sink.options, PAIMON_OPTION_WHITELIST, "paimon"
    )
    return value


def render_pipeline_sql(
    spec: PipelineSpec | Mapping[str, Any],
    *,
    source_profile_options: Mapping[str, Any],
    sink_profile_options: Mapping[str, Any],
    source_secret_options: Optional[Mapping[str, str]] = None,
    sink_secret_options: Optional[Mapping[str, str]] = None,
) -> str:
    """Render executable SQL from a frozen spec and runtime-resolved profiles."""
    parsed = parse_pipeline_spec(spec)
    normalized = normalized_spec(parsed)

    source_options = {
        "connector": "kafka",
        "topic": parsed.source.topic,
        "group.id": parsed.source.consumer_group,
        "format": parsed.source.format,
        "scan.startup.mode": parsed.source.startup_mode,
        **_source_profile_options(source_profile_options),
        **dict(source_secret_options or {}),
        **normalized["source"]["options"],
    }
    sink_options = normalized["sink"]["options"]
    catalog_options = {
        **_sink_catalog_options(sink_profile_options),
        **dict(sink_secret_options or {}),
    }

    columns = []
    for field in parsed.schema_fields:
        nullable = "" if field.nullable else " NOT NULL"
        columns.append(f"  {_identifier(field.name)} {field.data_type.upper()}{nullable}")
    if parsed.sink.primary_keys:
        keys = ", ".join(_identifier(key) for key in parsed.sink.primary_keys)
        columns.append(f"  PRIMARY KEY ({keys}) NOT ENFORCED")
    source_ddl = (
        "CREATE TEMPORARY TABLE `pipeline_source` (\n"
        + ",\n".join(
            f"  {_identifier(field.name)} {field.data_type.upper()}"
            + ("" if field.nullable else " NOT NULL")
            for field in (parsed.source_schema_fields or parsed.schema_fields)
        )
        + "\n) WITH (\n"
        + ",\n".join(
            f"  {_literal(key)} = {_literal(value)}" for key, value in sorted(source_options.items())
        )
        + "\n);"
    )
    catalog_name = "`pipeline_paimon`"
    catalog_ddl = (
        f"CREATE CATALOG IF NOT EXISTS {catalog_name} WITH (\n"
        + ",\n".join(
            f"  {_literal(key)} = {_literal(value)}"
            for key, value in sorted(catalog_options.items())
        )
        + "\n);"
    )
    # Keep the catalog explicit so generated SQL never depends on a session's current catalog.
    sink_name = ".".join(
        (catalog_name, _identifier(parsed.sink.database), _identifier(parsed.sink.table))
    )
    partition_clause = (
        "\nPARTITIONED BY ("
        + ", ".join(_identifier(key) for key in parsed.sink.partition_keys)
        + ")"
        if parsed.sink.partition_keys
        else ""
    )
    sink_with = (
        "\nWITH (\n"
        + ",\n".join(
            f"  {_literal(key)} = {_literal(value)}"
            for key, value in sorted(sink_options.items())
        )
        + "\n)"
        if sink_options
        else ""
    )
    sink_ddl = (
        f"CREATE TABLE IF NOT EXISTS {sink_name} (\n"
        + ",\n".join(columns)
        + "\n)"
        + partition_clause
        + sink_with
        + ";"
    )
    if parsed.transform and parsed.transform.projections:
        select_list = ",\n  ".join(
            f"{parsed.transform.projections[field.name]} AS {_identifier(field.name)}"
            for field in parsed.schema_fields
        )
    else:
        select_list = ", ".join(_identifier(field.name) for field in parsed.schema_fields)
    where = f"\nWHERE {parsed.transform.filter}" if parsed.transform and parsed.transform.filter else ""
    insert = f"INSERT INTO {sink_name}\nSELECT {select_list}\nFROM `pipeline_source`{where};"
    return "\n\n".join((source_ddl, catalog_ddl, sink_ddl, insert)) + "\n"


def compile_pipeline(spec: PipelineSpec | Mapping[str, Any]) -> dict:
    parsed = parse_pipeline_spec(spec)
    normalized = normalized_spec(parsed)
    spec_hash = hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
    source_profile_ref = {
        "bootstrap.servers": f"${{profile:{parsed.source.connection_profile_id}:bootstrap.servers}}"
    }
    sink_profile_ref = {
        "warehouse": f"${{profile:{parsed.sink.connection_profile_id}:warehouse}}"
    }
    sql = render_pipeline_sql(
        parsed,
        source_profile_options=source_profile_ref,
        sink_profile_options=sink_profile_ref,
    )

    artifact = {
        "artifact_version": "1.0",
        "compiler_version": COMPILER_VERSION,
        "definition_kind": "pipeline",
        "spec_hash": spec_hash,
        "sql": sql,
        "runner": {
            "type": "flink_sql",
            "source_connection_profile_id": parsed.source.connection_profile_id,
            "sink_connection_profile_id": parsed.sink.connection_profile_id,
            "secret_resolution": "runtime_profile_refs",
        },
    }
    artifact["artifact_hash"] = hashlib.sha256(
        canonical_json(artifact).encode("utf-8")
    ).hexdigest()
    return artifact

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy

import pytest

from app.services.stream_pipeline_compiler import (
    PipelineCompileError,
    compile_pipeline,
    render_pipeline_sql,
)
from app.services.stream_pipeline_schema import (
    diff_schema,
    discover_paimon_tables,
    discover_schema_registry_subjects,
    preflight_pipeline,
)
from app.services.stream_pipeline_spec import PipelineSpec


def _spec() -> dict:
    return {
        "spec_version": "1.0",
        "kind": "kafka_to_paimon",
        "mode": "upsert",
        "source": {
            "connection_profile_id": 11,
            "topic": "customer-events",
            "consumer_group": "gido-customer",
            "format": "json",
            "options": {"json.ignore-parse-errors": "true"},
        },
        "sink": {
            "connection_profile_id": 12,
            "database": "ods",
            "table": "customers",
            "primary_keys": ["id"],
            "options": {"merge-engine": "deduplicate", "bucket": "4"},
        },
        "schema": [
            {"name": "id", "data_type": "BIGINT", "nullable": False},
            {"name": "name", "data_type": "STRING", "nullable": True},
        ],
    }


def test_compiler_is_deterministic_and_contains_no_profile_secrets():
    first = compile_pipeline(_spec())
    reordered = copy.deepcopy(_spec())
    reordered["sink"]["options"] = {"bucket": "4", "merge-engine": "deduplicate"}
    second = compile_pipeline(reordered)

    assert first == second
    assert first["spec_hash"] == second["spec_hash"]
    assert first["artifact_hash"] == second["artifact_hash"]
    assert "connection_profile_id" in str(first["runner"])
    assert "password" not in str(first).lower()
    assert "CREATE CATALOG IF NOT EXISTS `pipeline_paimon`" in first["sql"]
    assert "${profile:11:bootstrap.servers}" in first["sql"]
    assert "${profile:12:warehouse}" in first["sql"]
    assert "PRIMARY KEY (`id`) NOT ENFORCED" in first["sql"]


def test_runtime_renderer_injects_profiles_without_changing_frozen_spec():
    sql = render_pipeline_sql(
        _spec(),
        source_profile_options={
            "bootstrap.servers": "kafka-1:9092",
            "security.protocol": "SASL_SSL",
        },
        sink_profile_options={"warehouse": "s3://warehouse/paimon"},
        source_secret_options={
            "properties.sasl.jaas.config": 'login required username="u" password="p";'
        },
    )
    assert "'properties.bootstrap.servers' = 'kafka-1:9092'" in sql
    assert "'warehouse' = 's3://warehouse/paimon'" in sql
    assert "'properties.sasl.jaas.config'" in sql
    assert "CREATE TABLE IF NOT EXISTS `pipeline_paimon`.`ods`.`customers`" in sql


def test_projection_order_follows_target_schema_not_alias_sorting():
    value = _spec()
    value["schema"] = [
        {"name": "z_value", "data_type": "BIGINT", "nullable": False},
        {"name": "a_value", "data_type": "STRING", "nullable": True},
    ]
    value["sink"]["primary_keys"] = ["z_value"]
    value["transform"] = {
        "projections": {"a_value": "`name`", "z_value": "`id`"}
    }
    sql = compile_pipeline(value)["sql"]
    assert sql.index("`id` AS `z_value`") < sql.index("`name` AS `a_value`")


def test_source_schema_supports_field_renames():
    value = _spec()
    value["source_schema"] = [
        {"name": "customer_id", "data_type": "BIGINT", "nullable": False},
        {"name": "customer_name", "data_type": "STRING", "nullable": True},
    ]
    value["transform"] = {
        "projections": {
            "id": "`customer_id`",
            "name": "`customer_name`",
        }
    }
    sql = compile_pipeline(value)["sql"]
    source_ddl = sql.split("CREATE CATALOG", 1)[0]
    assert "`customer_id` BIGINT NOT NULL" in source_ddl
    assert "`id` BIGINT NOT NULL" not in source_ddl


def test_compiler_rejects_unknown_and_inline_secret_options():
    value = _spec()
    value["source"]["options"]["properties.security.protocol"] = "SASL_SSL"
    with pytest.raises(PipelineCompileError, match="unsupported kafka"):
        compile_pipeline(value)

    value = _spec()
    value["sink"]["options"]["password"] = "plaintext"
    with pytest.raises(PipelineCompileError, match="may not contain secrets"):
        compile_pipeline(value)


def test_modes_have_distinct_semantic_requirements():
    value = _spec()
    value["mode"] = "append"
    value["sink"]["primary_keys"] = []
    assert PipelineSpec.model_validate(value).mode.value == "append"

    value["mode"] = "upsert"
    with pytest.raises(ValueError, match="requires sink.primary_keys"):
        PipelineSpec.model_validate(value)

    value = _spec()
    value["mode"] = "cdc"
    with pytest.raises(ValueError, match="CDC envelope"):
        PipelineSpec.model_validate(value)


def test_transform_rejects_statement_injection_and_incomplete_mapping():
    value = _spec()
    value["transform"] = {
        "projections": {"id": "`id`; DROP TABLE x", "name": "`name`"}
    }
    with pytest.raises(ValueError, match="statement delimiters"):
        PipelineSpec.model_validate(value)

    value = _spec()
    value["transform"] = {"projections": {"id": "`id`"}}
    with pytest.raises(ValueError, match="every target schema field"):
        PipelineSpec.model_validate(value)


def test_schema_registry_and_paimon_discovery_adapters(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return ["orders-value", "orders-key"]

    monkeypatch.setattr(
        "app.services.stream_pipeline_schema.requests.get",
        lambda *args, **kwargs: Response(),
    )
    assert discover_schema_registry_subjects(
        "https://registry.internal",
        basic_auth="user:password",
    ) == ["orders-key", "orders-value"]
    with pytest.raises(ValueError, match="blocked local address"):
        discover_schema_registry_subjects("http://169.254.169.254/latest")

    class S3:
        def list_objects_v2(self, **kwargs):
            if kwargs["Prefix"] == "warehouse/":
                return {"CommonPrefixes": [{"Prefix": "warehouse/ods/"}]}
            return {"CommonPrefixes": [{"Prefix": "warehouse/ods/orders/"}]}

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: S3())
    assert discover_paimon_tables(
        {"warehouse": "s3://lake/warehouse"}
    ) == [
        {
            "database": "ods",
            "table": "orders",
            "path": "s3://lake/warehouse/ods/orders",
        }
    ]


def test_schema_diff_and_preflight_block_breaking_changes():
    previous = [
        {"name": "id", "data_type": "INT", "nullable": False},
        {"name": "name", "data_type": "STRING", "nullable": True},
    ]
    compatible = diff_schema(previous, _spec()["schema"])
    assert compatible["compatible"] is True

    proposed = copy.deepcopy(_spec())
    proposed["schema"] = [{"name": "id", "data_type": "STRING", "nullable": False}]
    result = preflight_pipeline(
        proposed,
        previous_schema=previous,
        connection_profiles={11: "kafka", 12: "paimon"},
    )
    assert result["ok"] is False
    assert result["schema_diff"]["compatible"] is False

    strict = preflight_pipeline(
        _spec(),
        previous_schema=previous,
        connection_profiles={11: "kafka", 12: "paimon"},
    )
    assert strict["ok"] is False
    additive_spec = _spec()
    additive_spec["schema_evolution"] = "additive"
    additive = preflight_pipeline(
        additive_spec,
        previous_schema=previous,
        connection_profiles={11: "kafka", 12: "paimon"},
    )
    assert additive["ok"] is True


def test_preflight_validates_connection_profile_types():
    result = preflight_pipeline(
        _spec(), connection_profiles={11: "paimon", 12: "paimon"}
    )
    assert result["ok"] is False
    assert result["checks"][0]["status"] == "failed"

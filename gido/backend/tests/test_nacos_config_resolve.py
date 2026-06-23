# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.nacos_config_resolve import (
    build_connection_preview,
    extract_connections_from_env,
    flatten_mapping,
    parse_config_to_flat_map,
    resolve_placeholders,
)


def test_flatten_mapping_nested():
    data = {
        "env": {
            "flink": {
                "ticdc-betcenter": {
                    "kafka": {"connector": {"servers": "10.1.2.3:9092"}},
                }
            }
        }
    }
    flat = flatten_mapping(data)
    assert flat["env.flink.ticdc-betcenter.kafka.connector.servers"] == "10.1.2.3:9092"


def test_resolve_placeholders():
    env = {"env.flink.ticdc.kafka.connector.servers": "10.0.0.1:9092"}
    val, missing = resolve_placeholders(
        "${env.flink.ticdc.kafka.connector.servers}", env
    )
    assert val == "10.0.0.1:9092"
    assert missing == []


def test_build_connection_preview_from_job_ref():
    job = (
        "flink.ticdc.kafka.connector.servers="
        "${env.flink.ticdc-betcenter.kafka.connector.servers}\n"
    )
    common = """
env:
  flink:
    ticdc-betcenter:
      kafka:
        connector:
          servers: 10.65.20.1:9092,10.65.20.2:9092
"""
    out = build_connection_preview(
        job, "job.yml", common, "cipher-aes-data-warehouse-config-common.yml"
    )
    assert len(out["connections"]) == 1
    assert out["connections"][0]["value"] == "10.65.20.1:9092,10.65.20.2:9092"
    assert out["connections"][0]["kind"] == "kafka"


def test_sensitive_keys_excluded_from_env_scan():
    env = {
        "env.db.password": "secret123",
        "env.flink.kafka.connector.servers": "10.0.0.1:9092",
    }
    conns = extract_connections_from_env(env)
    assert len(conns) == 1
    assert conns[0]["value"] == "10.0.0.1:9092"


def test_parse_properties_flat():
    text = "env.flink.kafka.connector.servers=10.0.0.1:9092\nenv.db.password=xxx"
    flat = parse_config_to_flat_map(text, "env.properties")
    assert flat["env.flink.kafka.connector.servers"] == "10.0.0.1:9092"
    assert flat["env.db.password"] == "xxx"

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timezone

import pytest

from app.services.flink_runtime_catalog import BUNDLED_CONNECTORS
from app.services.stream_dlq_domain import (
    RawByteDlqUnsupported,
    build_dlq_envelope,
    create_replay_audit,
    dlq_capability,
)
from app.services.stream_observability import (
    Availability,
    FlinkRestObservabilityAdapter,
    KafkaLagAdapter,
    PaimonCommitAdapter,
    PrometheusQueryAdapter,
    S3PaimonCommitProvider,
    redact_mapping,
    summarize_lag_slo,
)
from app.services.stream_placement_policy import (
    JobPlacementRequirements,
    PlacementGroup,
    PlacementMode,
    UnsafeRegroupError,
    assert_auto_regroup_allowed,
    decide_placement,
    recommend_resources,
)


def test_kafka_runtime_connector_declares_flink_20_compatibility():
    kafka = next(connector for connector in BUNDLED_CONNECTORS if connector["id"] == "kafka")
    assert kafka["version"] == "4.0.1-2.0"
    assert kafka["connector"] == "kafka"
    assert kafka["compatibility"]["flink_release_line"] == "2.0.x"
    assert kafka["compatibility"]["runtime_verified"] == "2.0.1"


def test_placement_is_deterministic_and_capacity_aware():
    requirements = JobPlacementRequirements(
        job_id="job-1",
        requested_mode=PlacementMode.GROUPED,
        parallelism=2,
        expected_records_per_second=30_000,
    )
    groups = (
        PlacementGroup(group_id="group-b", capacity_slots=8, used_slots=1),
        PlacementGroup(group_id="group-a", capacity_slots=8, used_slots=1),
        PlacementGroup(group_id="full", capacity_slots=2, used_slots=2),
    )
    decision = decide_placement(requirements, iter(reversed(groups)))
    assert decision.mode == PlacementMode.GROUPED
    assert decision.target_group_id == "group-a"
    assert decision.resources.task_slots == 3
    assert decision.considered_groups == ("full", "group-a", "group-b")


def test_placement_isolates_critical_and_custom_dependency_jobs():
    requirements = JobPlacementRequirements(
        job_id="critical",
        requested_mode=PlacementMode.GROUPED,
        sla_tier="critical",
        custom_dependencies=("s3://artifacts/private.jar",),
    )
    decision = decide_placement(requirements, [PlacementGroup(group_id="shared", capacity_slots=20)])
    assert decision.mode == PlacementMode.DEDICATED
    assert {"critical_sla", "custom_dependency_isolation"} <= set(decision.reason_codes)


def test_existing_job_cannot_be_auto_regrouped():
    requirements = JobPlacementRequirements(
        job_id="moving",
        requested_mode=PlacementMode.GROUPED,
        existing_group_id="old",
    )
    decision = decide_placement(
        requirements,
        [PlacementGroup(group_id="new", capacity_slots=20)],
    )
    assert decision.auto_apply is False
    assert decision.safe_to_regroup is False
    assert "unsafe_auto_regroup_forbidden" in decision.reason_codes
    with pytest.raises(UnsafeRegroupError):
        assert_auto_regroup_allowed(requirements, decision)


def test_resource_recommendation_accounts_for_state_and_sla():
    baseline = recommend_resources(JobPlacementRequirements(job_id="a"))
    demanding = recommend_resources(
        JobPlacementRequirements(
            job_id="b",
            sla_tier="high",
            stateful=True,
            state_size_gb=20,
            expected_records_per_second=80_000,
        )
    )
    assert demanding.task_slots > baseline.task_slots
    assert demanding.memory_mb > baseline.memory_mb
    assert demanding.checkpoint_storage_gb == 60


class _KafkaProvider:
    def consumer_group_offsets(self, group_id, topic):
        return {0: 90, 1: 30}

    def topic_end_offsets(self, topic):
        return {0: 100, 1: 50}


def test_kafka_adapter_reports_offsets_lag_and_slo():
    observation = KafkaLagAdapter(_KafkaProvider()).observe(topic="events", group_id="consumer")
    assert observation.status == Availability.AVAILABLE
    assert observation.data["total_lag"] == 30
    summary = summarize_lag_slo(observation, maximum_lag=50)
    assert summary.compliant is True
    assert summary.error_budget_remaining == 20


def test_partial_kafka_offsets_do_not_report_slo_compliance():
    class PartialProvider:
        def consumer_group_offsets(self, group_id, topic):
            return {0: 90}

        def topic_end_offsets(self, topic):
            return {0: 100, 1: 500}

    observation = KafkaLagAdapter(PartialProvider()).observe(
        topic="events", group_id="new-consumer"
    )
    assert observation.status == Availability.PARTIAL
    assert observation.data["total_lag"] is None
    assert observation.data["unknown_partitions"] == [1]
    summary = summarize_lag_slo(observation, maximum_lag=50)
    assert summary.compliant is None
    assert summary.actual is None


def test_adapters_return_safe_unavailable_responses():
    class FailingKafka:
        def consumer_group_offsets(self, group_id, topic):
            raise RuntimeError("Bearer top-secret")

        def topic_end_offsets(self, topic):
            return {}

    class FailingPaimon:
        def latest_commits(self, table_identifier, limit):
            raise RuntimeError("https://admin:password@catalog.invalid failed")

    kafka = KafkaLagAdapter(FailingKafka()).observe(topic="events", group_id="consumer")
    paimon = PaimonCommitAdapter(FailingPaimon()).observe(table_identifier="catalog.db.table")
    assert kafka.status == Availability.UNAVAILABLE
    assert "top-secret" not in kafka.error
    assert paimon.status == Availability.UNAVAILABLE
    assert "password" not in paimon.error
    assert kafka.data == {}


def test_flink_adapter_combines_runtime_signals():
    def get_json(url, params):
        if url.endswith("/metrics"):
            return [
                {"id": "numRecordsInPerSecond", "value": "12.5"},
                {"id": "numRecordsOutPerSecond", "value": "11"},
                {"id": "backPressuredTimeMsPerSecond", "value": "4"},
                {"id": "numRestarts", "value": "2"},
            ]
        return {"counts": {"completed": 3}, "latest": {"completed": {"id": 9}}}

    result = FlinkRestObservabilityAdapter("http://flink", get_json=get_json).observe(job_id="abc")
    assert result.status == Availability.AVAILABLE
    assert result.data["throughput"]["records_in_per_second"] == 12.5
    assert result.data["backpressure_ms_per_second"] == 4.0
    assert result.data["restart_count"] == 2.0
    assert result.data["checkpoint"]["latest_completed"]["id"] == 9


def test_prometheus_adapter_returns_named_query_snapshots():
    def get_json(url, params):
        assert url.endswith("/api/v1/query")
        return {
            "status": "success",
            "data": {"result": [{"metric": {"job": "pipeline"}, "value": [1, "12"]}]},
        }

    result = PrometheusQueryAdapter(
        "http://prometheus", get_json=get_json
    ).observe(queries={"records_in": "metric_name"})
    assert result.status == Availability.AVAILABLE
    assert result.data["queries"]["records_in"][0]["value"][1] == "12"


def test_s3_paimon_provider_reads_latest_snapshot_metadata(monkeypatch):
    class Body:
        def read(self):
            return b'{"id":12,"schemaId":3,"commitKind":"APPEND","timeMillis":99}'

    class S3:
        def list_objects_v2(self, **kwargs):
            return {
                "Contents": [
                    {"Key": "warehouse/ods/orders/snapshot/snapshot-11"},
                    {"Key": "warehouse/ods/orders/snapshot/snapshot-12"},
                ]
            }

        def get_object(self, **kwargs):
            assert kwargs["Key"].endswith("snapshot-12")
            return {"Body": Body()}

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: S3())
    commits = S3PaimonCommitProvider(
        "s3://lake/warehouse"
    ).latest_commits("ods.orders", 1)
    assert commits[0]["snapshot_id"] == 12
    assert commits[0]["commit_kind"] == "APPEND"


def test_redaction_and_dlq_domain_are_explicit_about_raw_bytes():
    redacted = redact_mapping(
        {"password": "secret", "nested": {"authorization": "Bearer abc"}, "url": "https://u:p@host"}
    )
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["authorization"] == "[REDACTED]"
    assert "u:p" not in redacted["url"]
    with pytest.raises(RawByteDlqUnsupported):
        dlq_capability(require_raw_bytes=True)

    when = datetime(2026, 7, 31, tzinfo=timezone.utc)
    envelope = build_dlq_envelope(
        source="kafka:events",
        payload={"id": 1},
        error_code="VALIDATION",
        error_message="bad record",
        failed_at=when,
    )
    audit_a = create_replay_audit(
        dlq_record_id="record-1",
        envelope=envelope,
        requested_by="user-1",
        target="kafka:replay",
        reason="schema fixed",
        requested_at=when,
    )
    audit_b = create_replay_audit(
        dlq_record_id="record-1",
        envelope=envelope,
        requested_by="user-1",
        target="kafka:replay",
        reason="schema fixed",
        requested_at=when,
    )
    assert envelope.raw_bytes_captured is False
    assert audit_a == audit_b

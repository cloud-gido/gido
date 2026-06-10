# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
from app.services.flink_operator_submit import build_flink_deployment_body, build_flink_deployment_body_for_sql
from app.services.operator_resources import resolve_operator_resources, split_streaming_properties_for_operator


def test_resolve_operator_resources_defaults(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_JM_MEMORY", "1024m")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_TM_MEMORY", "2048m")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JM_CPU", 0.5)
    monkeypatch.setattr(settings, "FLINK_OPERATOR_TM_CPU", 1.5)
    monkeypatch.setattr(settings, "FLINK_OPERATOR_TASK_SLOTS", 4)
    monkeypatch.setattr(settings, "FLINK_OPERATOR_UPGRADE_MODE", "savepoint")

    res = resolve_operator_resources(None)
    assert res.jm_memory == "1024m"
    assert res.tm_memory == "2048m"
    assert res.jm_cpu == 0.5
    assert res.tm_cpu == 1.5
    assert res.task_slots == 4
    assert res.upgrade_mode == "savepoint"


def test_resolve_operator_resources_job_overrides():
    res = resolve_operator_resources({
        "jobManager": {"memory": "4096m", "cpu": 2},
        "taskManager": {"memory": "8192m", "cpu": 4, "replicas": 3},
        "taskSlots": 8,
        "upgradeMode": "stateless",
        "flinkConfiguration": {"execution.checkpointing.interval": "30s"},
    })
    assert res.jm_memory == "4096m"
    assert res.jm_cpu == 2.0
    assert res.tm_memory == "8192m"
    assert res.tm_cpu == 4.0
    assert res.tm_replicas == 3
    assert res.task_slots == 8
    assert res.flink_configuration["execution.checkpointing.interval"] == "30s"


def test_split_streaming_properties_for_operator():
    rest, res = split_streaming_properties_for_operator({
        "execution.checkpointing.interval": "60s",
        "operator_resources": {"taskSlots": 6},
        "k8s_application": {"jobmanager.memory.process.size": "1600m"},
    })
    assert rest == {"execution.checkpointing.interval": "60s"}
    assert res.task_slots == 6


def test_build_flink_deployment_body_with_resources():
    from app.services.operator_resources import OperatorResources

    resources = OperatorResources(
        jm_memory="3072m",
        jm_cpu=1.5,
        tm_memory="6144m",
        tm_cpu=2.0,
        task_slots=6,
        tm_replicas=2,
        upgrade_mode="stateless",
        flink_configuration={"state.backend": "hashmap"},
    )
    body = build_flink_deployment_body(
        deployment_name="gido-jar-9",
        namespace="flink",
        jar_uri="http://backend/j.jar",
        entry_class="com.example.Job",
        parallelism=8,
        operator_resources=resources,
    )
    assert body["spec"]["jobManager"]["resource"]["memory"] == "3072m"
    assert body["spec"]["taskManager"]["resource"]["cpu"] == 2.0
    assert body["spec"]["taskManager"]["replicas"] == 2
    assert body["spec"]["flinkConfiguration"]["taskmanager.numberOfTaskSlots"] == "6"
    assert body["spec"]["job"]["parallelism"] == 8
    assert body["spec"]["flinkConfiguration"]["user.artifacts.raw-http-enabled"] == "true"


def test_build_flink_deployment_body_for_sql_has_configmap_mount(monkeypatch):
    from app.core.config import settings
    from app.services.gido_deployment_meta import GidoDeploymentMeta

    monkeypatch.setattr(settings, "FLINK_OPERATOR_SQL_RUNNER_JAR_URI", "local:///opt/flink/usrlib/sql-runner.jar")
    meta = GidoDeploymentMeta(workspace_id=3, job_id=7, job_type="sql", sql_version="9", sql_hash="deadbeef")
    body = build_flink_deployment_body_for_sql(
        deployment_name="gido-sql-3-7",
        namespace="flink",
        sql_script_path="/opt/flink/gido-scripts/artifact.sql",
        parallelism=2,
        configmap_name="gido-sql-script-3-7",
        deployment_meta=meta,
    )
    assert body["metadata"]["labels"]["gido.io/workspace-id"] == "3"
    assert body["metadata"]["annotations"]["gido.io/sql-version"] == "9"
    assert body["spec"]["job"]["entryClass"] == "com.gido.flink.SqlRunner"
    assert body["spec"]["job"]["args"] == ["/opt/flink/gido-scripts/artifact.sql"]
    assert body["spec"]["podTemplate"]["spec"]["volumes"][0]["configMap"]["name"] == "gido-sql-script-3-7"


def test_resource_preset_merge():
    from app.services.operator_resources import split_streaming_properties_for_operator

    _, res = split_streaming_properties_for_operator({
        "resource_tier": "small",
        "operator_resources": {"taskManager": {"memory": "3072m"}},
    })
    assert res.jm_memory == "1024m"
    assert res.tm_memory == "3072m"
    assert res.task_slots == 2

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.flink_operator_submit import build_flink_deployment_body, build_flink_deployment_body_for_sql
from app.services.flink_pod_scheduling import (
    merge_pod_templates,
    operator_paimon_warehouse_pod_template,
    operator_scheduling_pod_template,
)


def test_operator_scheduling_pod_template_from_pool(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_NODE_POOL", "bigdata")
    tpl = operator_scheduling_pod_template()
    assert tpl is not None
    spec = tpl["spec"]
    assert spec["nodeSelector"]["node.gamelinelab.com/pool"] == "bigdata"
    assert spec["tolerations"][0]["value"] == "bigdata"


def test_build_flink_deployment_includes_scheduling(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_NODE_POOL", "bigdata")
    body = build_flink_deployment_body(
        deployment_name="gido-jar-1",
        namespace="flink",
        jar_uri="s3://b/a.jar",
        entry_class="com.example.Job",
        parallelism=1,
    )
    spec = body["spec"]["podTemplate"]["spec"]
    assert spec["nodeSelector"]["node.gamelinelab.com/pool"] == "bigdata"


def test_sql_pod_template_merges_scheduling_and_configmap(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_NODE_POOL", "bigdata")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_SQL_RUNNER_JAR_URI", "local:///opt/flink/usrlib/sql-runner.jar")
    body = build_flink_deployment_body_for_sql(
        deployment_name="gido-sql-0-1",
        namespace="flink",
        sql_script_path="/opt/flink/gido-scripts/artifact.sql",
        parallelism=1,
        configmap_name="gido-sql-script-0-1",
    )
    pt = body["spec"]["podTemplate"]["spec"]
    assert pt["nodeSelector"]["node.gamelinelab.com/pool"] == "bigdata"
    assert pt["volumes"][0]["configMap"]["name"] == "gido-sql-script-0-1"
    assert pt["containers"][0]["volumeMounts"][0]["mountPath"] == "/opt/flink/gido-scripts"
    assert pt["containers"][0]["imagePullPolicy"] == "Always"


def test_operator_paimon_warehouse_pod_template_file_warehouse(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "file:///opt/flink/paimon-warehouse")
    tpl = operator_paimon_warehouse_pod_template()
    assert tpl is not None
    assert tpl["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "paimon-warehouse"
    mounts = tpl["spec"]["containers"][0]["volumeMounts"]
    assert mounts[0]["mountPath"] == "/opt/flink/paimon-warehouse"


def test_operator_paimon_warehouse_skipped_for_s3(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://bucket/wh")
    assert operator_paimon_warehouse_pod_template() is None


def test_sql_pod_template_includes_paimon_pvc(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "file:///opt/flink/paimon-warehouse")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_SQL_RUNNER_JAR_URI", "local:///opt/flink/usrlib/sql-runner.jar")
    body = build_flink_deployment_body_for_sql(
        deployment_name="gido-sql-0-2",
        namespace="flink",
        sql_script_path="/opt/flink/gido-scripts/artifact.sql",
        parallelism=1,
        configmap_name="gido-sql-script-0-2",
    )
    pt = body["spec"]["podTemplate"]["spec"]
    vol_names = {v["name"] for v in pt["volumes"]}
    assert "paimon-warehouse" in vol_names
    assert "gido-sql-script" in vol_names


def test_merge_pod_templates_preserves_both():
    scheduling = {
        "spec": {
            "nodeSelector": {"node.gamelinelab.com/pool": "bigdata"},
            "tolerations": [{"key": "node.gamelinelab.com/pool", "value": "bigdata"}],
        }
    }
    sql = {
        "spec": {
            "volumes": [{"name": "gido-sql-script", "configMap": {"name": "cm-1"}}],
            "containers": [
                {
                    "name": "flink-main-container",
                    "volumeMounts": [{"name": "gido-sql-script", "mountPath": "/opt/flink/gido-scripts"}],
                }
            ],
        }
    }
    merged = merge_pod_templates(scheduling, sql)
    assert merged["spec"]["nodeSelector"]["node.gamelinelab.com/pool"] == "bigdata"
    assert merged["spec"]["volumes"][0]["configMap"]["name"] == "cm-1"

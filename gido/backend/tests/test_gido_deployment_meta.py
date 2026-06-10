# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
from app.services.gido_deployment_meta import (
    GidoDeploymentMeta,
    jar_deployment_name,
    sql_configmap_name,
    sql_deployment_name,
    sql_script_hash,
)


def test_sql_deployment_name_with_workspace():
    assert sql_deployment_name(12, 42) == "gido-sql-12-42"
    assert jar_deployment_name(3, 7) == "gido-jar-3-7"
    assert len(sql_deployment_name(999999, 888888)) <= 63


def test_sql_configmap_name():
    assert sql_configmap_name(5, 9) == "gido-sql-script-5-9"


def test_sql_script_hash_stable():
    h1 = sql_script_hash("SELECT 1;")
    h2 = sql_script_hash("SELECT 1;")
    assert h1 == h2
    assert len(h1) == 64


def test_gido_deployment_meta_annotations():
    meta = GidoDeploymentMeta(
        workspace_id=10,
        job_id=20,
        job_type="sql",
        sql_version="15",
        sql_hash="abc123",
        submitted_by="admin",
        submitted_at="2026-06-10T10:00:00+00:00",
    )
    ann = meta.annotations()
    assert ann["gido.io/workspace-id"] == "10"
    assert ann["gido.io/job-id"] == "20"
    assert ann["gido.io/sql-version"] == "15"
    assert ann["gido.io/sql-hash"] == "abc123"
    body = {"metadata": {"name": "gido-sql-10-20", "namespace": "flink"}}
    meta.apply_to_body(body)
    assert body["metadata"]["labels"]["gido.io/workspace-id"] == "10"

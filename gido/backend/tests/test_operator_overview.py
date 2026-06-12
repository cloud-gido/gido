# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_meta.db")

from app.services.flink_operator_submit import deployment_summary_from_cr, operator_overview_payload


def test_deployment_summary_from_cr_stable():
    cr = {
        "metadata": {
            "name": "gido-sql-1-42",
            "namespace": "bigdata",
            "creationTimestamp": "2026-06-01T00:00:00Z",
            "labels": {
                "gido.io/workspace-id": "1",
                "gido.io/job-id": "42",
                "gido.io/job-type": "sql",
            },
        },
        "spec": {
            "image": "ghcr.io/acme/gido-flink-runtime:2.0.1",
            "flinkVersion": "v2_0",
            "job": {"state": "running"},
        },
        "status": {
            "lifecycleState": "STABLE",
            "jobStatus": {"jobId": "abc123"},
            "clusterInfo": {
                "jobManagerStatus": {"status": "READY"},
                "taskManagerStatus": {"status": "READY", "replicas": 2},
            },
        },
    }
    row = deployment_summary_from_cr(cr)
    assert row["name"] == "gido-sql-1-42"
    assert row["health"] == "healthy"
    assert row["flink_job_id"] == "abc123"
    assert row["job_manager_status"]["status"] == "READY"


def test_deployment_summary_suspended():
    cr = {
        "metadata": {"name": "gido-jar-1-7", "labels": {}},
        "spec": {"job": {"state": "suspended"}},
        "status": {"lifecycleState": "SUSPENDED"},
    }
    row = deployment_summary_from_cr(cr)
    assert row["health"] == "suspended"
    assert row["spec_state"] == "suspended"


def test_operator_overview_payload_no_k8s(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(
        "app.services.flink_operator_submit.kubernetes_api_available",
        lambda: False,
    )
    monkeypatch.setattr(settings, "FLINK_OPERATOR_NAMESPACE", "bigdata")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_IMAGE", "ghcr.io/acme/gido-flink-runtime:latest")
    payload = operator_overview_payload(workspace_id=1)
    assert payload["namespace"] == "bigdata"
    assert payload["runtime"]["runtime_image"] == "ghcr.io/acme/gido-flink-runtime:latest"
    assert payload["deployments"] == []
    assert payload["k8s_error"]

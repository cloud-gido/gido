# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-10
from app.services.flink_operator_submit import build_flink_deployment_body, deployment_name_for_job


def test_deployment_name_for_job():
    assert deployment_name_for_job(42, 12) == "gido-jar-12-42"
    assert len(deployment_name_for_job(999999, 1)) <= 63


def test_build_flink_deployment_body_structure():
    body = build_flink_deployment_body(
        deployment_name="gido-jar-1",
        namespace="flink",
        jar_uri="http://host.docker.internal:8001/api/streaming/jobs/1/artifact.jar?token=t",
        entry_class="com.example.Job",
        parallelism=4,
        program_args="--k v",
    )
    assert body["kind"] == "FlinkDeployment"
    assert body["metadata"]["name"] == "gido-jar-1"
    assert body["spec"]["flinkVersion"] == "v2_0"
    assert body["spec"]["job"]["entryClass"] == "com.example.Job"
    assert body["spec"]["job"]["jarURI"].startswith("http://")
    assert body["spec"]["job"]["args"] == ["--k", "v"]
    assert body["spec"]["flinkConfiguration"]["kubernetes.rest-service.exposed.type"] == "LoadBalancer"
    assert body["spec"]["flinkConfiguration"]["taskmanager.numberOfTaskSlots"] == "2"


def test_extract_sql_set_flink_configuration():
    from app.services.flink_operator_submit import extract_sql_set_flink_configuration

    sql = """
SET 'execution.runtime-mode' = 'batch';
SET 'fs.s3a.access.key' = 'AKIA123';
SET 'fs.s3a.secret.key' = 'sec/ret';
SET 'fs.s3a.endpoint' = 's3.us-east-2.amazonaws.com';
"""
    props = extract_sql_set_flink_configuration(sql)
    assert props["fs.s3a.access.key"] == "AKIA123"
    assert props["fs.s3a.secret.key"] == "sec/ret"
    assert "execution.runtime-mode" not in props


def test_apply_flink_deployment_replace_sets_resource_version(monkeypatch):
    from app.services import flink_operator_submit as fos

    calls = []

    class FakeApi:
        def create_namespaced_custom_object(self, **kw):
            from kubernetes.client import ApiException

            raise ApiException(status=409, reason="Conflict")

        def get_namespaced_custom_object(self, **kw):
            calls.append("get")
            return {"metadata": {"resourceVersion": "12345", "uid": "uid-1"}}

        def replace_namespaced_custom_object(self, **kw):
            calls.append("replace")
            body = kw.get("body") or {}
            assert body["metadata"]["resourceVersion"] == "12345"
            assert body["metadata"]["uid"] == "uid-1"
            return body

    monkeypatch.setattr(fos, "_custom_objects_api", lambda: FakeApi())
    body = {"metadata": {"name": "gido-jar-9", "namespace": "flink"}, "spec": {}}
    out = fos.apply_flink_deployment(body)
    assert calls == ["get", "replace"]
    assert out["metadata"]["resourceVersion"] == "12345"


def test_pipeline_runtime_secret_is_injected_via_secret_env(monkeypatch):
    from app.services import flink_operator_submit as fos

    captured = {}

    class FakeCoreApi:
        def create_namespaced_secret(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(fos, "_core_v1_api", lambda: FakeCoreApi())
    name = fos.ensure_sql_runtime_secret(
        "pipeline-secrets",
        {"GIDO_PIPELINE_SECRET_ABC": "super-secret"},
        "flink",
    )
    assert name == "pipeline-secrets"
    assert captured["body"]["stringData"] == {
        "GIDO_PIPELINE_SECRET_ABC": "super-secret"
    }
    template = fos._build_pod_template_for_runtime_secret(name)
    container = template["spec"]["containers"][0]
    assert container["envFrom"] == [
        {"secretRef": {"name": "pipeline-secrets"}}
    ]


def test_sql_deployment_delete_cleans_runtime_secret(monkeypatch):
    from app.services import flink_operator_submit as fos

    cleaned = []

    class FakeCustomApi:
        def delete_namespaced_custom_object(self, **kwargs):
            return {}

    monkeypatch.setattr(fos, "_custom_objects_api", lambda: FakeCustomApi())
    monkeypatch.setattr(
        fos,
        "delete_sql_runtime_secret",
        lambda deployment_name, namespace=None: cleaned.append(
            (deployment_name, namespace)
        ),
    )
    assert fos.delete_flink_deployment("gido-sql-1-2", "flink") is True
    assert cleaned == [("gido-sql-1-2", "flink")]


def test_sql_deployment_delete_ignores_secret_cleanup_errors(monkeypatch):
    from app.services import flink_operator_submit as fos

    class FakeCustomApi:
        def delete_namespaced_custom_object(self, **kwargs):
            return {}

    monkeypatch.setattr(fos, "_custom_objects_api", lambda: FakeCustomApi())

    def boom(deployment_name, namespace=None):
        from kubernetes.client import ApiException

        raise ApiException(status=403, reason="Forbidden")

    monkeypatch.setattr(fos, "delete_sql_runtime_secret", boom)
    assert fos.delete_flink_deployment("gido-sql-1-237", "bigdata") is True


def test_artifact_token_stable_without_internal_token(monkeypatch):
    from app.core.config import settings
    from app.services import jar_artifact as ja

    monkeypatch.setattr(settings, "FLINK_OPERATOR_ARTIFACT_TOKEN", "fixed-artifact-token")
    monkeypatch.setattr(settings, "INTERNAL_TOKEN", "jwt-should-not-be-used")
    assert ja.resolved_artifact_download_token() == "fixed-artifact-token"
    assert ja.artifact_download_token_is_valid("fixed-artifact-token")
    assert not ja.artifact_download_token_is_valid("jwt-should-not-be-used")


def test_kubernetes_api_available_in_cluster(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(fos.settings, "FLINK_K8S_KUBECONFIG_PATH", "")
    assert not fos.kubernetes_api_available()

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    assert fos.kubernetes_api_available()


def test_resolve_operator_jm_rest_production_uses_cluster_dns(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_DEV_LOCAL", False)
    monkeypatch.setattr(
        fos.settings,
        "FLINK_OPERATOR_JM_REST_TEMPLATE",
        "http://{deployment_name}-rest.{namespace}.svc.cluster.local:8081",
    )
    url = fos.resolve_operator_jm_rest("gido-jar-3", "flink")
    assert url == "http://gido-jar-3-rest.flink.svc.cluster.local:8081"


def test_browser_jm_base_skips_cluster_dns_fallback(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_UI_URL_TEMPLATE", "")
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_BROWSER_JM_BASE", "")
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_AUTO_UI_TUNNEL", False)
    monkeypatch.setattr(fos, "kubernetes_api_available", lambda: False)
    internal = "http://gido-jar-1-rest.flink.svc.cluster.local:8081"
    assert fos.browser_jm_base_for_deployment("gido-jar-1", "flink", internal, job_id=1) is None


def test_browser_jm_base_uses_ui_proxy_when_enabled(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_UI_PROXY_ENABLED", True)
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_UI_URL_TEMPLATE", "")
    url = fos.browser_jm_base_for_deployment("gido-jar-1", "flink", None, job_id=1)
    assert url == "/api/streaming/jobs/1/flink-ui"


def test_browser_jm_base_prefers_browser_jm_base_over_cluster_dns(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_UI_PROXY_ENABLED", False)
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_UI_URL_TEMPLATE", "")
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_BROWSER_JM_BASE", "http://127.0.0.1:8081")
    url = fos.browser_jm_base_for_deployment(
        "gido-jar-1",
        "flink",
        "http://gido-jar-1-rest.flink.svc.cluster.local:8081",
        job_id=1,
    )
    assert url == "http://127.0.0.1:8081"


def test_port_forward_hint_for_localhost_browser_base(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_BROWSER_JM_BASE", "http://127.0.0.1:8081")
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_AUTO_UI_TUNNEL", False)
    hint = fos.operator_ui_port_forward_hint("gido-jar-1", "flink", "http://127.0.0.1:8081")
    assert hint is not None
    assert "gido-jar-1-rest" in hint


def test_resolve_operator_jm_rest_dev_local_skips_cluster_dns(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_DEV_LOCAL", True)
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_AUTO_UI_TUNNEL", False)
    monkeypatch.setattr(fos.settings, "FLINK_K8S_KUBECONFIG_PATH", "")
    monkeypatch.setattr(fos.settings, "FLINK_K8S_REST_EXPOSED_TYPE", "LoadBalancer")
    url = fos.resolve_operator_jm_rest("gido-jar-3", "flink", job_id=3)
    assert url is None


def test_patch_suspend_and_resume_lifecycle_fields(monkeypatch):
    from app.services import flink_operator_submit as fos

    calls = []

    class FakeApi:
        def patch_namespaced_custom_object(self, **kwargs):
            calls.append(kwargs)
            return kwargs["body"]

    monkeypatch.setattr(fos, "_custom_objects_api", lambda: FakeApi())
    fos.suspend_flink_deployment("job-1", "operator-ns")
    fos.resume_flink_deployment("job-1", "operator-ns", restart_nonce=7)

    assert calls[0]["namespace"] == "operator-ns"
    assert calls[0]["body"] == {
        "spec": {"job": {"state": "suspended", "upgradeMode": "savepoint"}}
    }
    assert calls[1]["body"] == {
        "spec": {
            "job": {
                "state": "running",
                "upgradeMode": "savepoint",
                "restartNonce": 7,
            }
        }
    }


def test_extract_savepoint_status_across_operator_field_names():
    from app.services import flink_operator_submit as fos

    completed = {
        "status": {
            "jobStatus": {
                "savepointInfo": {
                    "triggerStatus": "in_progress",
                    "lastSavepoint": {"location": "s3://bucket/savepoint-1"},
                }
            }
        }
    }
    assert fos.extract_savepoint_status_from_cr(completed) == (
        "IN_PROGRESS",
        "s3://bucket/savepoint-1",
        None,
    )

    failed = {
        "status": {
            "job_status": {
                "savepoint_info": {
                    "state": "pending",
                    "last_savepoint": {"failure_cause": "checkpoint storage unavailable"},
                }
            }
        }
    }
    assert fos.extract_savepoint_status_from_cr(failed) == (
        "FAILED",
        None,
        "checkpoint storage unavailable",
    )


def test_extract_savepoint_status_from_upgrade_savepoint_path():
    from app.services import flink_operator_submit as fos

    cr = {
        "status": {
            "jobStatus": {
                "savepointInfo": {
                    "lastPeriodicSavepointTimestamp": 0,
                    "savepointHistory": [],
                },
                "upgradeSavepointPath": "s3a://bucket/flink/savepoints/savepoint-xyz",
            }
        }
    }
    assert fos.extract_savepoint_status_from_cr(cr) == (
        "COMPLETED",
        "s3a://bucket/flink/savepoints/savepoint-xyz",
        None,
    )


def test_extract_completed_savepoint_from_snapshots_ignores_old_path():
    from app.services import flink_operator_submit as fos

    snaps = [
        {
            "metadata": {"name": "old-snap"},
            "spec": {"savepoint": {}, "jobReference": {"name": "job-1"}},
            "status": {"state": "COMPLETED", "path": "s3://old"},
        },
        {
            "metadata": {"name": "new-snap"},
            "spec": {"savepoint": {}, "jobReference": {"name": "job-1"}},
            "status": {"state": "COMPLETED", "path": "s3://new"},
        },
    ]
    path, err = fos.extract_completed_savepoint_from_snapshots(
        snaps, previous_path="s3://old"
    )
    assert path == "s3://new"
    assert err is None

    path2, err2 = fos.extract_completed_savepoint_from_snapshots(
        snaps,
        ignore_paths={"s3://old", "s3://new"},
        ignore_names={"old-snap", "new-snap"},
    )
    assert path2 is None
    assert err2 is None


def test_collect_completed_savepoint_snapshot_idents():
    from app.services import flink_operator_submit as fos

    paths, names = fos.collect_completed_savepoint_snapshot_idents(
        [
            {
                "metadata": {"name": "sp-1"},
                "spec": {"savepoint": {}},
                "status": {"state": "COMPLETED", "path": "s3://a"},
            },
            {
                "metadata": {"name": "sp-pending"},
                "spec": {"savepoint": {}},
                "status": {"state": "IN_PROGRESS"},
            },
        ]
    )
    assert paths == {"s3://a"}
    assert names == {"sp-1"}


def test_suspend_patches_savepoint_dirs(monkeypatch):
    from app.services import flink_operator_submit as fos

    calls = []

    class FakeApi:
        def patch_namespaced_custom_object(self, **kwargs):
            calls.append(kwargs)
            return kwargs["body"]

    monkeypatch.setattr(fos, "_custom_objects_api", lambda: FakeApi())
    fos.suspend_flink_deployment(
        "job-1",
        "operator-ns",
        savepoint_dir="s3a://bucket/flink/savepoints",
    )
    assert calls[0]["body"] == {
        "spec": {
            "job": {"state": "suspended", "upgradeMode": "savepoint"},
            "flinkConfiguration": {
                "state.savepoints.dir": "s3a://bucket/flink/savepoints",
                "execution.checkpointing.savepoint-dir": "s3a://bucket/flink/savepoints",
            },
        }
    }


def test_wait_for_completed_savepoint_success(monkeypatch):
    from app.services import flink_operator_submit as fos

    responses = iter(
        [
            {
                "status": {
                    "jobStatus": {
                        "savepointInfo": {"triggerId": "trigger-1", "status": "PENDING"}
                    }
                }
            },
            {
                "status": {
                    "jobStatus": {
                        "savepointInfo": {
                            "lastSavepoint": {"path": "s3://bucket/savepoint-2"}
                        }
                    }
                }
            },
        ]
    )
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: next(responses))
    monkeypatch.setattr(fos, "list_flink_state_snapshots", lambda **k: [])
    monkeypatch.setattr(fos.time, "sleep", lambda _: None)

    assert (
        fos.wait_for_completed_savepoint(
            "job-1", "operator-ns", timeout_seconds=5, poll_interval_seconds=0
        )
        == "s3://bucket/savepoint-2"
    )


def test_wait_for_completed_savepoint_via_upgrade_path(monkeypatch):
    from app.services import flink_operator_submit as fos

    responses = iter(
        [
            {"status": {"jobStatus": {"savepointInfo": {"savepointHistory": []}}}},
            {
                "status": {
                    "jobStatus": {
                        "upgradeSavepointPath": "s3a://bucket/sp-upgrade",
                        "savepointInfo": {"savepointHistory": []},
                    }
                }
            },
        ]
    )
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: next(responses))
    monkeypatch.setattr(fos, "list_flink_state_snapshots", lambda **k: [])
    monkeypatch.setattr(fos.time, "sleep", lambda _: None)
    assert (
        fos.wait_for_completed_savepoint(
            "job-1", timeout_seconds=5, poll_interval_seconds=0
        )
        == "s3a://bucket/sp-upgrade"
    )


def test_wait_for_completed_savepoint_via_flink_state_snapshot(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(
        fos,
        "read_flink_deployment",
        lambda *a, **k: {
            "status": {"jobStatus": {"savepointInfo": {"savepointHistory": []}}}
        },
    )
    monkeypatch.setattr(
        fos,
        "list_flink_state_snapshots",
        lambda **k: [
            {
                "spec": {"savepoint": {}, "jobReference": {"name": "job-1"}},
                "status": {
                    "state": "COMPLETED",
                    "path": "s3a://bucket/from-snapshot",
                },
            }
        ],
    )
    monkeypatch.setattr(fos.time, "sleep", lambda _: None)
    assert (
        fos.wait_for_completed_savepoint(
            "job-1", timeout_seconds=5, poll_interval_seconds=0
        )
        == "s3a://bucket/from-snapshot"
    )


def test_wait_for_completed_savepoint_ignores_previous_path(monkeypatch):
    from app.services import flink_operator_submit as fos

    responses = iter(
        [
            {
                "status": {
                    "jobStatus": {
                        "savepointInfo": {
                            "lastSavepoint": {"path": "s3://bucket/old"}
                        }
                    }
                }
            },
            {
                "status": {
                    "jobStatus": {
                        "savepointInfo": {
                            "lastSavepoint": {"path": "s3://bucket/new"}
                        }
                    }
                }
            },
        ]
    )
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: next(responses))
    monkeypatch.setattr(fos, "list_flink_state_snapshots", lambda **k: [])
    monkeypatch.setattr(fos.time, "sleep", lambda _: None)

    assert fos.wait_for_completed_savepoint(
        "job-1",
        timeout_seconds=5,
        poll_interval_seconds=0,
        previous_path="s3://bucket/old",
    ) == "s3://bucket/new"


def test_wait_for_completed_savepoint_failure_never_returns_path(monkeypatch):
    import pytest

    from app.services import flink_operator_submit as fos

    cr = {
        "status": {
            "jobStatus": {
                "savepointInfo": {
                    "lastSavepoint": {
                        "location": "s3://bucket/incomplete",
                        "failureCause": "savepoint failed",
                    }
                }
            }
        }
    }
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: cr)
    monkeypatch.setattr(fos, "list_flink_state_snapshots", lambda **k: [])
    monkeypatch.setattr(fos.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="savepoint failed"):
        fos.wait_for_completed_savepoint("job-1", timeout_seconds=0)


def test_wait_for_suspended_success_and_timeout(monkeypatch):
    import pytest

    from app.services import flink_operator_submit as fos

    suspended = {
        "spec": {"job": {"state": "suspended"}},
        "status": {
            "jobStatus": {"state": "SUSPENDED"},
            "reconciliationStatus": {"state": "DEPLOYED"},
        },
    }
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: suspended)
    assert fos.wait_for_flink_deployment_suspended("job-1", timeout_seconds=0) is suspended

    pending = {
        "spec": {"job": {"state": "suspended"}},
        "status": {"reconciliationStatus": {"state": "UPGRADING"}},
    }
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: pending)
    with pytest.raises(TimeoutError, match="to suspend"):
        fos.wait_for_flink_deployment_suspended("job-1", timeout_seconds=0)


def test_wait_for_running_requires_reconciled_job(monkeypatch):
    import pytest

    from app.services import flink_operator_submit as fos

    running = {
        "spec": {"job": {"state": "running"}},
        "status": {"jobStatus": {"state": "RUNNING", "jobId": "jid-1"}},
    }
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: running)
    assert fos.wait_for_flink_deployment_running("job-1", timeout_seconds=0) is running

    pending = {
        "spec": {"job": {"state": "running"}},
        "status": {"jobStatus": {"state": "SUSPENDED", "jobId": "old-jid"}},
    }
    monkeypatch.setattr(fos, "read_flink_deployment", lambda *a, **k: pending)
    with pytest.raises(TimeoutError, match="to resume"):
        fos.wait_for_flink_deployment_running("job-1", timeout_seconds=0)


def test_prepare_flink_deployment_for_savepoint_redeploy_is_non_mutating():
    from app.services import flink_operator_submit as fos

    original = {"spec": {"job": {"state": "running"}}}
    prepared = fos.prepare_flink_deployment_for_savepoint_redeploy(
        original,
        "s3://bucket/savepoint-3",
        savepoint_redeploy_nonce=11,
        allow_non_restored_state=True,
    )

    assert original == {"spec": {"job": {"state": "running"}}}
    assert prepared["spec"]["job"] == {
        "state": "running",
        "initialSavepointPath": "s3://bucket/savepoint-3",
        "savepointRedeployNonce": 11,
        "allowNonRestoredState": True,
    }


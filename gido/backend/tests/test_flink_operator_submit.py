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


def test_sql_deployment_name_for_job():
    from app.services.flink_operator_submit import sql_deployment_name_for_job

    assert sql_deployment_name_for_job(7, 3) == "gido-sql-3-7"


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


def test_flink_conf_injects_s3_irsa_when_s3_artifacts_enabled(monkeypatch):
    from app.core.config import settings
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(settings, "FLINK_OPERATOR_S3_USE_IRSA", True)
    monkeypatch.setattr(
        settings,
        "FLINK_OPERATOR_S3_CREDENTIALS_PROVIDER",
        "com.amazonaws.auth.WebIdentityTokenCredentialsProvider",
    )
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_S3_PREFIX", "s3://bucket/flink/job-jar")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_CHECKPOINT_DIR", "")
    conf = fos._base_flink_conf()
    assert conf["fs.s3a.aws.credentials.provider"] == (
        "com.amazonaws.auth.WebIdentityTokenCredentialsProvider"
    )


def test_resolve_operator_jm_rest_dev_local_skips_cluster_dns(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_DEV_LOCAL", True)
    monkeypatch.setattr(fos.settings, "FLINK_OPERATOR_AUTO_UI_TUNNEL", False)
    monkeypatch.setattr(fos.settings, "FLINK_K8S_KUBECONFIG_PATH", "")
    monkeypatch.setattr(fos.settings, "FLINK_K8S_REST_EXPOSED_TYPE", "LoadBalancer")
    url = fos.resolve_operator_jm_rest("gido-jar-3", "flink", job_id=3)
    assert url is None


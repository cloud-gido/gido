# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

import pytest

from app.services import flink_jm_gateway as gw


def test_jm_rest_template_for_gateway_host():
    assert gw.jm_rest_template_for_gateway_host("jm-gw.example.internal") == (
        "http://jm-gw.example.internal/jm/{namespace}/{deployment_name}"
    )
    assert gw.jm_rest_template_for_gateway_host("http://jm-gw.example.internal") == (
        "http://jm-gw.example.internal/jm/{namespace}/{deployment_name}"
    )


def test_jm_rest_template_includes_nonstandard_port(monkeypatch):
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_PORT", 8081)
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_URL_PORT", None)
    assert gw.jm_rest_template_for_gateway_host("jm-gw.example.internal") == (
        "http://jm-gw.example.internal:8081/jm/{namespace}/{deployment_name}"
    )


def test_jm_rest_template_url_port_override(monkeypatch):
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_PORT", 8081)
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_URL_PORT", 80)
    assert gw.jm_rest_template_for_gateway_host("jm-gw.example.internal") == (
        "http://jm-gw.example.internal/jm/{namespace}/{deployment_name}"
    )


def test_resolve_gateway_host_explicit():
    p = SimpleNamespace(id=3, name="prod-a", flink_operator_jm_gateway_host="jm-gw.prod.local")
    assert gw.resolve_gateway_host(p) == "jm-gw.prod.local"


def test_resolve_gateway_host_from_suffix(monkeypatch):
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_HOST_SUFFIX", "flink.internal")
    p = SimpleNamespace(id=5, name="Cluster A", flink_operator_jm_gateway_host=None)
    assert gw.resolve_gateway_host(p) == "jm-gw-cluster-a-p5.flink.internal"


def test_resolve_gateway_host_requires_host_or_suffix(monkeypatch):
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_HOST_SUFFIX", None)
    p = SimpleNamespace(id=1, name="x", flink_operator_jm_gateway_host=None)
    with pytest.raises(ValueError, match="flink_operator_jm_gateway_host"):
        gw.resolve_gateway_host(p)


def test_nginx_config_contains_cluster_domain():
    body = gw._nginx_configmap_body("gido-jm-gw-1", "gido-flink-gateway", "cluster.local", "10.96.0.10", {})
    conf = body["data"][gw._NGINX_CONF_KEY]
    assert "listen 8080;" in conf
    assert "resolver 10.96.0.10 valid=10s" in conf
    assert "svc.cluster.local:8081" in conf
    assert "/jm/" in conf


def test_nginx_config_custom_listen_port(monkeypatch):
    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_PORT", 8081)
    body = gw._nginx_configmap_body("gido-jm-gw-1", "gido-flink-gateway", "cluster.local", "10.96.0.10", {})
    conf = body["data"][gw._NGINX_CONF_KEY]
    assert "listen 8081;" in conf


def test_cluster_dns_resolver_ip_from_coredns(monkeypatch):
    class FakeMeta:
        name = "coredns"

    class FakeSpec:
        cluster_ip = "172.20.0.10"
        cluster_ips = None

    class FakeSvc:
        metadata = FakeMeta()
        spec = FakeSpec()

    class FakeList:
        items = [FakeSvc()]

    class FakeCore:
        def list_namespaced_service(self, namespace):
            if namespace == "kube-system":
                return FakeList()
            from kubernetes.client import ApiException
            raise ApiException(status=404)

    monkeypatch.setattr(gw.settings, "FLINK_OPERATOR_JM_GATEWAY_DNS_IP", None)
    assert gw._cluster_dns_resolver_ip(FakeCore(), "cluster.local") == "172.20.0.10"


def test_cluster_dns_resolver_ip_profile_override():
    class FakeCore:
        def list_namespaced_service(self, namespace):
            raise AssertionError("should not list when override set")

    assert gw._cluster_dns_resolver_ip(FakeCore(), "cluster.local", dns_override="10.43.0.10") == "10.43.0.10"


def test_ingress_body_uses_host_and_class():
    body = gw._ingress_body("gido-jm-gw-2", "gido-flink-gateway", "http://jm-gw.test.local", "nginx-internal", {})
    assert body["spec"]["ingressClassName"] == "nginx-internal"
    assert body["spec"]["rules"][0]["host"] == "jm-gw.test.local"


def test_provision_jm_gateway_calls_k8s(monkeypatch):
    calls = []

    class FakeMeta:
        resource_version = "1"
        uid = "uid-1"

    class FakeIngress:
        status = None

    class FakeCore:
        def create_namespace(self, body):
            calls.append(("ns", body))

        def create_namespaced_config_map(self, namespace, body):
            calls.append(("cm", namespace, body["metadata"]["name"]))
            return body

        def replace_namespaced_config_map(self, namespace, name, body):
            calls.append(("cm-replace", name))
            return body

        def read_namespaced_config_map(self, name, namespace):
            return SimpleNamespace(metadata=FakeMeta())

        def create_namespaced_service(self, namespace, body):
            calls.append(("svc", body["metadata"]["name"]))
            return body

        def replace_namespaced_service(self, namespace, name, body):
            return body

        def read_namespaced_service(self, name, namespace):
            return SimpleNamespace(metadata=FakeMeta())

    class FakeApps:
        def create_namespaced_deployment(self, namespace, body):
            calls.append(("dep", body["metadata"]["name"]))
            return body

        def replace_namespaced_deployment(self, namespace, name, body):
            return body

        def read_namespaced_deployment(self, name, namespace):
            return SimpleNamespace(metadata=FakeMeta())

    class FakeNet:
        def create_namespaced_ingress(self, namespace, body):
            calls.append(("ing", body["spec"]["rules"][0]["host"]))
            return FakeIngress()

        def replace_namespaced_ingress(self, namespace, name, body):
            return FakeIngress()

        def read_namespaced_ingress(self, name, namespace):
            return SimpleNamespace(metadata=FakeMeta())

    monkeypatch.setattr(gw, "_cluster_dns_resolver_ip", lambda core, domain: "10.96.0.10")
    monkeypatch.setattr(gw, "kubernetes_api_available", lambda: True)
    monkeypatch.setattr(gw, "_k8s_clients", lambda ctx: (FakeCore(), FakeApps(), FakeNet()))

    profile = SimpleNamespace(
        id=9,
        name="cluster-b",
        flink_operator_jm_gateway_host="jm-gw.b.local",
        flink_operator_jm_gateway_namespace=None,
        flink_operator_jm_gateway_ingress_class="nginx",
    )
    ctx = gw.OperatorRuntimeContext.from_settings().with_overrides(
        profile_id=9,
        cluster_domain="cluster.local",
        kubeconfig_path="/tmp/k",
    )
    out = gw.provision_jm_gateway(profile, ctx)
    assert out["ready"] is True
    assert "jm-gw.b.local" in out["jm_rest_template"]
    assert ("ing", "jm-gw.b.local") in calls

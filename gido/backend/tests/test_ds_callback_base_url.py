# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Dolphin Worker 回调 GIDO 基址解析。"""
from app.core.config import settings
from app.services.dolphin import _ds_callback_curl, ds_callback_base_url


def test_ds_callback_base_url_prefers_explicit(monkeypatch):
    monkeypatch.setattr(settings, "GIDO_DS_CALLBACK_BASE_URL", "http://backend.bigdata.svc:8001/")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://other:8001")
    assert ds_callback_base_url() == "http://backend.bigdata.svc:8001"


def test_ds_callback_base_url_falls_back_to_jar_http(monkeypatch):
    monkeypatch.setattr(settings, "GIDO_DS_CALLBACK_BASE_URL", "")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", "http://backend.gido.svc:8001")
    assert ds_callback_base_url() == "http://backend.gido.svc:8001"


def test_ds_callback_base_url_default(monkeypatch):
    monkeypatch.setattr(settings, "GIDO_DS_CALLBACK_BASE_URL", None)
    monkeypatch.setattr(settings, "FLINK_OPERATOR_JAR_HTTP_BASE", None)
    assert ds_callback_base_url() == "http://gido-backend:8001"


def test_ds_callback_curl_includes_bizdate_and_token(monkeypatch):
    monkeypatch.setattr(settings, "GIDO_DS_CALLBACK_BASE_URL", "http://backend.bigdata.svc:8001")
    monkeypatch.setattr(settings, "INTERNAL_TOKEN", "tok-xyz")
    script = _ds_callback_curl(
        "/api/studio/internal/nodes/260/run",
        json_body='{"bizdate":"$[yyyy-MM-dd]"}',
    )
    assert "http://backend.bigdata.svc:8001/api/studio/internal/nodes/260/run" in script
    assert "Bearer tok-xyz" in script
    assert '"$[yyyy-MM-dd]"' in script or "$[yyyy-MM-dd]" in script

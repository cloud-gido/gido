# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_meta.db")

from pydantic import ValidationError

from app.services.flink_runtime_catalog import BUNDLED_CONNECTORS, flink_runtime_api_payload
from app.services.flink_submit_mode import (
    default_sql_submit_mode,
    enforce_jar_submit_mode_allowed,
    enforce_sql_submit_mode_allowed,
    normalize_jar_submit_mode,
    normalize_sql_submit_mode,
)


def test_default_sql_submit_mode_operator(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GIDO_FLINK_SUBMIT_MODE", "operator")
    monkeypatch.setattr(settings, "GIDO_LEGACY_FLINK_SUBMIT", False)
    assert default_sql_submit_mode() == "flink_operator"
    assert normalize_sql_submit_mode(None) == "flink_operator"
    assert normalize_sql_submit_mode("session") == "flink_operator"


def test_legacy_session_allowed(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GIDO_LEGACY_FLINK_SUBMIT", True)
    assert normalize_sql_submit_mode("session") == "session"
    assert enforce_sql_submit_mode_allowed("session") == "session"
    assert normalize_jar_submit_mode("session") == "session"
    assert enforce_jar_submit_mode_allowed("session") == "session"


def test_legacy_disabled_rejects_session(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "GIDO_LEGACY_FLINK_SUBMIT", False)
    try:
        enforce_sql_submit_mode_allowed("session")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "GIDO_LEGACY_FLINK_SUBMIT" in str(e)
    try:
        enforce_jar_submit_mode_allowed("session")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "GIDO_LEGACY_FLINK_SUBMIT" in str(e)


def test_job_update_flink_operator(monkeypatch):
    from app.api.streaming import JobUpdate
    from app.core.config import settings

    monkeypatch.setattr(settings, "GIDO_LEGACY_FLINK_SUBMIT", False)
    ju = JobUpdate(flink_sql_submit_mode="flink_operator", flink_jar_submit_mode="flink_operator")
    assert ju.flink_sql_submit_mode == "flink_operator"
    assert ju.flink_jar_submit_mode == "flink_operator"


def test_job_create_defaults_operator(monkeypatch):
    from app.api.streaming import JobCreate
    from app.core.config import settings

    monkeypatch.setattr(settings, "GIDO_LEGACY_FLINK_SUBMIT", False)
    jc = JobCreate(workspace_id=1, name="t", job_type="SQL")
    assert jc.flink_sql_submit_mode == "flink_operator"
    assert jc.flink_jar_submit_mode == "flink_operator"


def test_job_update_rejects_session_without_legacy(monkeypatch):
    from app.api.streaming import JobUpdate
    from app.core.config import settings

    monkeypatch.setattr(settings, "GIDO_LEGACY_FLINK_SUBMIT", False)
    try:
        JobUpdate(flink_sql_submit_mode="session")
        assert False, "expected ValidationError"
    except ValidationError as e:
        assert "GIDO_LEGACY_FLINK_SUBMIT" in str(e)


def test_flink_runtime_api_payload(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLINK_OPERATOR_NAMESPACE", "flink")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_IMAGE", "registry/gido-flink-runtime:latest")
    monkeypatch.setattr(settings, "PAIMON_WAREHOUSE_DEFAULT", "s3://bucket/wh")
    monkeypatch.setattr(settings, "FLINK_OPERATOR_CHECKPOINT_DIR", "s3://bucket/checkpoints")
    payload = flink_runtime_api_payload()
    assert payload["submit_mode"] == "operator"
    assert payload["operator_namespace"] == "flink"
    assert payload["paimon_warehouse_default"] == "s3://bucket/wh"
    assert payload["checkpoint_dir_default"] == "s3://bucket/checkpoints"
    assert len(payload["connectors"]) == len(BUNDLED_CONNECTORS)
    assert any(c["id"] == "paimon" for c in payload["connectors"])

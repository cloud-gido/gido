# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""P0：状态同步只回填 GIDO；停止才删 CR。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///./pytest_gido_meta.db")

from app.api import streaming as streaming_api


def _job(**kwargs):
    base = dict(
        id=42,
        workspace_id=1,
        name="t",
        job_type="SQL",
        status="cancelled",
        flink_job_id=None,
        flink_operator_deployment_name="gido-sql-1-42",
        flink_application_jm_rest=None,
        flink_application_cluster_id=None,
        flink_sql_submit_mode="flink_operator",
        flink_jar_submit_mode="flink_operator",
        last_submit_error=None,
        updated_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _db():
    return MagicMock()


def _rt_cfg():
    return SimpleNamespace()


def test_apply_cr_running_corrects_cancelled_db(monkeypatch):
    """集群 CR 仍在 → 即使库内 cancelled 也纠正为 running；不删 CR。"""
    deleted = []

    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: deleted.append(a or k),
    )
    job = _job(status="cancelled", flink_job_id=None)
    cr = {
        "spec": {"job": {"state": "running"}},
        "status": {
            "lifecycleState": "STABLE",
            "jobStatus": {"jobId": "jid-abc"},
        },
    }
    early = streaming_api._apply_status_from_operator_cr(_db(), job, cr, "gido-sql-1-42")
    assert job.status == "running"
    assert job.flink_job_id == "jid-abc"
    assert early is None  # 有 jid 时继续查 JM
    assert deleted == []


def test_apply_cr_failed_sets_failed(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: deleted.append(True),
    )
    job = _job(status="running", flink_job_id="old")
    cr = {
        "spec": {"job": {"state": "running"}},
        "status": {
            "lifecycleState": "FAILED",
            "error": "JM OOM",
            "jobStatus": {},
        },
    }
    out = streaming_api._apply_status_from_operator_cr(_db(), job, cr, "gido-sql-1-42")
    assert job.status == "failed"
    assert job.last_submit_error == "JM OOM"
    assert out["status"] == "failed"
    assert out["flink_status"] == "FAILED"
    assert deleted == []


def test_sync_cr_missing_marks_cancelled_without_delete(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: deleted.append(True),
    )
    monkeypatch.setattr(streaming_api, "_flink_runtime_cfg_for_job", lambda db, job: _rt_cfg())
    monkeypatch.setattr(streaming_api, "_flink_client_for_job", lambda db, job: MagicMock())
    monkeypatch.setattr(streaming_api, "_jm_base_for_job", lambda *a, **k: None)
    monkeypatch.setattr(streaming_api, "_operator_deployment_name_for_job", lambda job: "gido-sql-1-42")
    monkeypatch.setattr(
        streaming_api,
        "_compute_flink_operational",
        lambda job, runtime_cfg=None: {"hints": []},
    )

    job = _job(status="running", flink_job_id="jid-1")
    out = streaming_api._sync_one_job_live_status(_db(), job, cr_cache={})
    assert out["status"] == "cancelled"
    assert out["flink_status"] == "NOT_FOUND_ON_OPERATOR"
    assert job.status == "cancelled"
    assert deleted == []


def test_sync_cr_running_never_deletes(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: deleted.append(True),
    )
    monkeypatch.setattr(streaming_api, "_flink_runtime_cfg_for_job", lambda db, job: _rt_cfg())
    fc = MagicMock()
    fc.fetch_job_document.return_value = {"state": "RUNNING"}
    monkeypatch.setattr(streaming_api, "_flink_client_for_job", lambda db, job: fc)
    monkeypatch.setattr(streaming_api, "_jm_base_for_job", lambda *a, **k: "http://jm:8081")
    monkeypatch.setattr(streaming_api, "_operator_deployment_name_for_job", lambda job: "gido-sql-1-42")
    monkeypatch.setattr(
        streaming_api,
        "_compute_flink_operational",
        lambda job, runtime_cfg=None: {"hints": []},
    )

    cr = {
        "spec": {"job": {"state": "running"}},
        "status": {
            "lifecycleState": "STABLE",
            "jobStatus": {"jobId": "jid-live"},
        },
    }
    job = _job(status="cancelled", flink_job_id=None)
    out = streaming_api._sync_one_job_live_status(
        _db(), job, cr_cache={"gido-sql-1-42": cr}, jm_timeout=1.0
    )
    assert job.status == "running"
    assert out["status"] == "running"
    assert out.get("flink_status") == "RUNNING"
    assert deleted == []


def test_cancel_job_deletes_flink_deployment(monkeypatch):
    deleted = []
    suspended = []

    def _delete(name, *a, **k):
        deleted.append(name)
        return True

    def _suspend(name, *a, **k):
        suspended.append(name)

    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        _delete,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.suspend_flink_deployment",
        _suspend,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.find_flink_deployment_names_for_job",
        lambda *a, **k: ["gido-sql-1-42"],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.list_flink_deployments",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.flink_deployment_still_exists",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit._operator_namespace",
        lambda: "flink",
    )
    monkeypatch.setattr(streaming_api, "_operator_deployment_name_for_job", lambda job: "gido-sql-1-42")
    monkeypatch.setattr(streaming_api, "_release_operator_ui_tunnel", lambda job: None)
    monkeypatch.setattr(
        streaming_api,
        "require_streaming_job",
        lambda db, user, job_id, *a, **k: _job(
            status="running",
            flink_job_id="jid-1",
            job_type="JAR",
            flink_sql_submit_mode="flink_operator",
        ),
    )

    user = SimpleNamespace(id=1)
    out = streaming_api.cancel_job(42, db=_db(), current_user=user)
    assert deleted == ["gido-sql-1-42"]
    assert "已删除 FlinkDeployment" in out["message"]


def test_cancel_job_fails_when_cr_still_exists(monkeypatch):
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.suspend_flink_deployment",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.find_flink_deployment_names_for_job",
        lambda *a, **k: ["gido-sql-1-42"],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.list_flink_deployments",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.flink_deployment_still_exists",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit._operator_namespace",
        lambda: "flink",
    )
    monkeypatch.setattr(streaming_api, "_operator_deployment_name_for_job", lambda job: "gido-sql-1-42")
    monkeypatch.setattr(streaming_api, "_release_operator_ui_tunnel", lambda job: None)
    monkeypatch.setattr(
        streaming_api,
        "require_streaming_job",
        lambda db, user, job_id, *a, **k: _job(status="running", flink_job_id="jid-1", job_type="SQL"),
    )

    from fastapi import HTTPException

    try:
        streaming_api.cancel_job(42, db=_db(), current_user=SimpleNamespace(id=1))
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 500
        assert "仍存在 FlinkDeployment" in str(e.detail)

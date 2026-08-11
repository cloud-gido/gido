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


def test_apply_cr_reconciles_failed_stop_operation(monkeypatch):
    """卡在 SAVING_STATE 但 stop 操作已失败 → 纠偏为仍运行中。"""
    job = _job(status="running", flink_job_id="jid-1", lifecycle_state="SAVING_STATE")
    failed_op = SimpleNamespace(
        status="failed",
        operation_type="stop",
        requested_at="2026-01-01",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = failed_op
    cr = {
        "spec": {"job": {"state": "running"}},
        "status": {
            "lifecycleState": "STABLE",
            "jobStatus": {"jobId": "jid-1"},
        },
    }
    out = streaming_api._apply_status_from_operator_cr(db, job, cr, "gido-sql-1-42")
    assert job.lifecycle_state == "RUNNING"
    assert job.status == "running"
    assert out["lifecycle_state"] == "RUNNING"
    assert out["status"] == "running"


def test_apply_cr_preserves_planned_stop_in_progress(monkeypatch):
    """计划停止进行中且操作未终态：即使 CR 已 suspended，也不抢先标已停止。"""
    deleted = []
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: deleted.append(True),
    )
    job = _job(status="running", flink_job_id="jid-1", lifecycle_state="SUSPENDING")
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = SimpleNamespace(
        status="running",
        operation_type="stop",
    )
    cr = {
        "spec": {"job": {"state": "suspended"}},
        "status": {
            "lifecycleState": "SUSPENDED",
            "jobStatus": {"jobId": "jid-1"},
        },
    }
    out = streaming_api._apply_status_from_operator_cr(db, job, cr, "gido-sql-1-42")
    assert job.status == "running"
    assert job.lifecycle_state == "SUSPENDING"
    assert job.flink_job_id == "jid-1"
    assert out["flink_status"] == "SUSPENDING"
    assert deleted == []


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
            "jobStatus": {"jobId": "jid-abc", "state": "RUNNING"},
            "taskManager": {"replicas": 1},
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

    job = _job(status="running", flink_job_id="jid-1", lifecycle_state="SUSPENDING")
    out = streaming_api._sync_one_job_live_status(_db(), job, cr_cache={})
    assert out["status"] == "cancelled"
    assert out["flink_status"] == "NOT_FOUND_ON_OPERATOR"
    assert out["lifecycle_state"] == "FORCE_STOPPED"
    assert job.status == "cancelled"
    assert job.lifecycle_state == "FORCE_STOPPED"
    assert deleted == []


def test_sync_cr_missing_during_restoring_keeps_lifecycle(monkeypatch):
    """重启 replace 删 CR 窗口：不得把 RESTORING 回填成 SUSPENDED。"""
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

    job = _job(status="running", flink_job_id="jid-1", lifecycle_state="RESTORING")
    out = streaming_api._sync_one_job_live_status(_db(), job, cr_cache={})
    assert out["flink_status"] == "NOT_FOUND_ON_OPERATOR"
    assert out["lifecycle_state"] == "RESTORING"
    assert job.status == "running"
    assert job.lifecycle_state == "RESTORING"
    assert deleted == []

    job2 = _job(status="running", flink_job_id=None, lifecycle_state="DEPLOYING")
    out2 = streaming_api._sync_one_job_live_status(_db(), job2, cr_cache={})
    assert out2["lifecycle_state"] == "DEPLOYING"
    assert job2.lifecycle_state == "DEPLOYING"
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
            "jobStatus": {"jobId": "jid-live", "state": "RUNNING"},
            "taskManager": {"replicas": 1},
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

    def _delete(name, namespace=None, *a, **k):
        deleted.append((namespace, name))
        return True

    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        _delete,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.find_flink_deployment_refs_for_job",
        lambda *a, **k: [("bigdata", "gido-sql-1-42")],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_flink_deployment_reclaimed",
        lambda *a, **k: "gone",
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit._operator_namespace",
        lambda: "bigdata",
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
    assert deleted == [("bigdata", "gido-sql-1-42")]
    assert "已删除 FlinkDeployment" in out["message"]
    assert "bigdata" in out.get("namespace", "")


def test_cancel_job_accepts_terminating(monkeypatch):
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.find_flink_deployment_refs_for_job",
        lambda *a, **k: [("bigdata", "gido-jar-1-204")],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_flink_deployment_reclaimed",
        lambda *a, **k: "terminating",
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit._operator_namespace",
        lambda: "bigdata",
    )
    monkeypatch.setattr(streaming_api, "_operator_deployment_name_for_job", lambda job: "gido-jar-1-204")
    monkeypatch.setattr(streaming_api, "_release_operator_ui_tunnel", lambda job: None)
    monkeypatch.setattr(
        streaming_api,
        "require_streaming_job",
        lambda db, user, job_id, *a, **k: _job(status="running", flink_job_id="jid-1", job_type="JAR"),
    )

    out = streaming_api.cancel_job(204, db=_db(), current_user=SimpleNamespace(id=1))
    assert out.get("terminating")
    assert "bigdata" in out.get("namespace", "")


def test_cancel_job_fails_when_cr_still_exists(monkeypatch):
    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.find_flink_deployment_refs_for_job",
        lambda *a, **k: [("bigdata", "gido-sql-1-42")],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_flink_deployment_reclaimed",
        lambda *a, **k: "exists",
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit._operator_namespace",
        lambda: "bigdata",
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
        assert "仍未进入回收" in str(e.detail)
        assert "bigdata" in str(e.detail)


def test_cancel_job_skips_forbidden_namespace_guess(monkeypatch):
    """无权 ns 上猜测 DELETE 会 403；只应对可读到的 CR 发删除。"""
    deleted = []

    def _delete(name, namespace=None, *a, **k):
        if namespace == "flink":
            raise PermissionError("forbidden flink")
        deleted.append((namespace, name))
        return True

    monkeypatch.setattr(
        "app.services.flink_operator_submit.delete_flink_deployment",
        _delete,
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.find_flink_deployment_refs_for_job",
        lambda *a, **k: [("bigdata", "gido-jar-1-204")],
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit.wait_flink_deployment_reclaimed",
        lambda *a, **k: "gone",
    )
    monkeypatch.setattr(
        "app.services.flink_operator_submit._operator_namespace",
        lambda: "flink",
    )
    monkeypatch.setattr(streaming_api, "_operator_deployment_name_for_job", lambda job: "gido-jar-1-204")
    monkeypatch.setattr(streaming_api, "_release_operator_ui_tunnel", lambda job: None)
    monkeypatch.setattr(
        streaming_api,
        "require_streaming_job",
        lambda db, user, job_id, *a, **k: _job(status="running", flink_job_id="jid-1", job_type="JAR"),
    )

    out = streaming_api.cancel_job(204, db=_db(), current_user=SimpleNamespace(id=1))
    assert deleted == [("bigdata", "gido-jar-1-204")]
    assert "bigdata" in out.get("namespace", "")


def test_find_refs_only_adds_accessible_preferred(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos, "_operator_namespace", lambda: "flink")
    monkeypatch.setattr(fos, "list_flink_deployments", lambda **k: [])

    def _accessible(name, namespace=None):
        return namespace == "flink" and name == "gido-jar-1-204"

    monkeypatch.setattr(fos, "flink_deployment_accessible", _accessible)
    refs = fos.find_flink_deployment_refs_for_job(204, preferred_name="gido-jar-1-204")
    assert refs == [("flink", "gido-jar-1-204")]


def test_operator_candidates_use_one_explicit_or_configured_namespace(monkeypatch):
    from app.services import flink_operator_submit as fos

    monkeypatch.setattr(fos, "_operator_namespace", lambda: "operator-system")
    assert fos._operator_namespace_candidates("flink") == ["flink"]
    assert fos._operator_namespace_candidates("bigdata") == ["bigdata"]
    assert fos._operator_namespace_candidates(None) == ["operator-system"]


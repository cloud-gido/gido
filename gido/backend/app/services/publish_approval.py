# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""发布审批：普通开发提交，空间/平台管理员审批后执行发布。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
import hashlib
import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.data_service import DataApi
from app.models.workspace import PublishApproval, TaskNode, User, Workflow
from app.services.rbac import (
    assert_workspace_access,
    check_workspace_permission,
    workspace_data_full_control,
)


VALID_RESOURCE_TYPES = frozenset({"workflow", "studio_node", "stream_job", "data_service_api"})
VALID_ACTIONS = frozenset({"publish_to_ds", "publish_node", "submit_job", "publish_api", "offline_api"})
TERMINAL_STATUSES = frozenset({"approved", "rejected", "cancelled"})

_RESOURCE_ACTIONS: dict[str, frozenset[str]] = {
    "workflow": frozenset({"publish_to_ds"}),
    "studio_node": frozenset({"publish_node"}),
    "stream_job": frozenset({"submit_job"}),
    "data_service_api": frozenset({"publish_api", "offline_api"}),
}


def _perm_for_resource(resource_type: str) -> str:
    from app.core import perm_codes as PC

    return {
        "workflow": PC.GIDO_BATCH_WORKFLOW_RUN,
        "studio_node": PC.GIDO_BATCH_STUDIO_RUN,
        "stream_job": PC.GIDO_STREAM_WRITE,
        "data_service_api": PC.GIDO_SERVICE_RUN,
    }[resource_type]


def _assert_approval_workspace_access(db: Session, user, workspace_id: int, resource_type: Optional[str] = None) -> None:
    """查看/提交审批：空间内 developer + 任一发布相关平台权限；管理员仅需进入空间。"""
    from app.core.access import assert_any_permission
    from app.core import perm_codes as PC

    if workspace_data_full_control(db, user, workspace_id):
        assert_workspace_access(db, user, workspace_id)
        return
    check_workspace_permission(db, user, workspace_id, "developer")
    if resource_type:
        from app.core.access import user_has_any

        if user_has_any(db, user, [_perm_for_resource(resource_type)]):
            return
        raise HTTPException(status_code=403, detail="无权提交该类型发布审批")
    assert_any_permission(
        db,
        user,
        PC.GIDO_BATCH_OPERATION_READ,
        PC.GIDO_BATCH_WORKFLOW_RUN,
        PC.GIDO_BATCH_STUDIO_RUN,
        PC.GIDO_STREAM_RUN,
        PC.GIDO_SERVICE_RUN,
    )


def _user_name(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    return u.username if u else None


def _resolve_resource(
    db: Session, workspace_id: int, resource_type: str, resource_id: int
) -> tuple[str, Any]:
    if resource_type == "workflow":
        wf = db.query(Workflow).filter(Workflow.id == resource_id, Workflow.workspace_id == workspace_id).first()
        if not wf:
            raise HTTPException(status_code=404, detail="工作流不存在")
        return wf.name, wf
    if resource_type == "studio_node":
        node = db.query(TaskNode).filter(TaskNode.id == resource_id, TaskNode.workspace_id == workspace_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="开发节点不存在")
        return node.name, node
    if resource_type == "stream_job":
        from app.api.streaming import StreamingJob

        job = db.query(StreamingJob).filter(StreamingJob.id == resource_id, StreamingJob.workspace_id == workspace_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="实时作业不存在")
        return job.name, job
    if resource_type == "data_service_api":
        api = db.query(DataApi).filter(DataApi.id == resource_id, DataApi.workspace_id == workspace_id).first()
        if not api:
            raise HTTPException(status_code=404, detail="数据服务 API 不存在")
        label = f"{api.name} ({api.api_code})"
        return label, api
    raise HTTPException(status_code=400, detail=f"不支持的资源类型: {resource_type}")


def serialize_approval(db: Session, row: PublishApproval) -> Dict[str, Any]:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "release_id": getattr(row, "release_id", None),
        "submitted_hash": getattr(row, "submitted_hash", None),
        "baseline_hash": getattr(row, "baseline_hash", None),
        "snapshot_schema_version": getattr(row, "snapshot_schema_version", 1),
        "resource_name": row.resource_name,
        "action": row.action,
        "status": row.status,
        "submit_note": row.submit_note,
        "review_note": row.review_note,
        "submitted_by": row.submitted_by,
        "submitted_by_username": _user_name(db, row.submitted_by),
        "reviewed_by": row.reviewed_by,
        "reviewed_by_username": _user_name(db, row.reviewed_by),
        "submitted_at": row.submitted_at,
        "reviewed_at": row.reviewed_at,
    }


def assert_can_publish_production(db: Session, user, workspace_id: int) -> None:
    """直接发布到生产：仅空间管理员或平台管理员。"""
    if not workspace_data_full_control(db, user, workspace_id):
        raise HTTPException(
            status_code=403,
            detail="仅空间管理员或平台管理员可直接发布到生产；普通开发请提交审批",
        )


def find_pending_approval(
    db: Session, workspace_id: int, resource_type: str, resource_id: int, action: str
) -> Optional[PublishApproval]:
    return (
        db.query(PublishApproval)
        .filter(
            PublishApproval.workspace_id == workspace_id,
            PublishApproval.resource_type == resource_type,
            PublishApproval.resource_id == resource_id,
            PublishApproval.action == action,
            PublishApproval.status == "pending",
        )
        .first()
    )


def submit_publish_approval(
    db: Session,
    user,
    workspace_id: int,
    resource_type: str,
    resource_id: int,
    action: str,
    submit_note: Optional[str] = None,
    release_id: Optional[int] = None,
) -> PublishApproval:
    if resource_type not in VALID_RESOURCE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的资源类型: {resource_type}")
    if action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"不支持的发布动作: {action}")
    allowed = _RESOURCE_ACTIONS.get(resource_type, frozenset())
    if action not in allowed:
        raise HTTPException(status_code=400, detail=f"{resource_type} 不支持动作 {action}")

    _assert_approval_workspace_access(db, user, workspace_id, resource_type)

    if workspace_data_full_control(db, user, workspace_id):
        raise HTTPException(status_code=400, detail="管理员可直接发布，无需提交审批")

    name, resource = _resolve_resource(db, workspace_id, resource_type, resource_id)
    if resource_type == "data_service_api":
        if action == "publish_api":
            has_pending = bool(getattr(resource, "pending_definition", None))
            if getattr(resource, "status", None) == "online" and not has_pending:
                raise HTTPException(status_code=400, detail="API 已上线且无待发布变更，无需重复发布")
            if getattr(resource, "status", None) not in ("draft", "offline", "online"):
                raise HTTPException(status_code=400, detail="当前状态不可发布")
        if action == "offline_api" and getattr(resource, "status", None) != "online":
            raise HTTPException(status_code=400, detail="仅已上线 API 可提交下线审批")

    existing = find_pending_approval(db, workspace_id, resource_type, resource_id, action)
    if existing:
        raise HTTPException(status_code=409, detail="已有待审批申请，请等待管理员处理")

    row = PublishApproval(
        workspace_id=workspace_id,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=name,
        action=action,
        status="pending",
        submit_note=(submit_note or "").strip() or None,
        submitted_by=user.id,
    )
    if resource_type == "stream_job":
        from app.api.streaming import StreamingJobRelease

        release_query = db.query(StreamingJobRelease).filter(
            StreamingJobRelease.job_id == resource_id,
            StreamingJobRelease.approval_status == "pending",
        )
        if release_id is not None:
            release_query = release_query.filter(
                StreamingJobRelease.id == release_id
            )
        else:
            release_query = release_query.order_by(
                StreamingJobRelease.version.desc()
            )
        release = release_query.first()
        if not release:
            raise HTTPException(status_code=409, detail="请先提交不可变实时作业发布版本")
        row.release_id = release.id
    submitted_snapshot, baseline_snapshot = _capture_approval_snapshots(
        db,
        resource_type,
        resource,
        action=action,
        release_id=getattr(row, "release_id", None),
    )
    row.submitted_snapshot = submitted_snapshot
    row.baseline_snapshot = baseline_snapshot
    row.submitted_hash = _snapshot_hash(submitted_snapshot)
    row.baseline_hash = _snapshot_hash(baseline_snapshot)
    row.snapshot_schema_version = 1
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _execute_approval_action(db: Session, row: PublishApproval, reviewer) -> Dict[str, Any]:
    if row.action == "publish_to_ds":
        from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
        from app.services.workflow_ds_publish import publish_workflow_to_ds

        wf = db.query(Workflow).filter(Workflow.id == row.resource_id).first()
        if not wf:
            raise HTTPException(status_code=404, detail="工作流不存在")
        if not get_dolphin_runtime(db, wf.workspace_id).enabled:
            raise HTTPException(status_code=400, detail="DolphinScheduler 未启用")
        refresh_ds_client(db, wf.workspace_id)
        return publish_workflow_to_ds(db, wf, published_by=reviewer.id if reviewer else None)

    if row.action == "publish_node":
        node = db.query(TaskNode).filter(TaskNode.id == row.resource_id).first()
        if not node:
            raise HTTPException(status_code=404, detail="开发节点不存在")
        node.is_published = True
        if settings.STUDIO_LOCK_ON_PUBLISH:
            node.is_locked = True
        node.updated_at = datetime.utcnow()
        db.commit()
        return {"message": "节点已发布"}

    if row.action == "submit_job":
        from app.api.streaming import (
            StreamingJob,
            StreamingJobRelease,
            approve_streaming_job_release,
        )

        job = db.query(StreamingJob).filter(StreamingJob.id == row.resource_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="实时作业不存在")
        release = db.query(StreamingJobRelease).filter(
            StreamingJobRelease.id == getattr(row, "release_id", None),
            StreamingJobRelease.job_id == job.id,
        ).first()
        if not release:
            raise HTTPException(status_code=409, detail="审批绑定的实时作业发布版本不存在")
        approve_streaming_job_release(
            db,
            job,
            release,
            reviewer.id if reviewer else row.reviewed_by,
            comment=row.review_note,
        )
        db.flush()
        return {
            "message": f"实时作业发布版本 v{release.version} 已批准，可在作业运维中部署",
            "release_id": release.id,
            "release_version": release.version,
        }

    if row.action == "publish_api":
        from app.services.data_service_publish import execute_data_api_publish

        api = db.query(DataApi).filter(DataApi.id == row.resource_id).first()
        if not api:
            raise HTTPException(status_code=404, detail="数据服务 API 不存在")
        return execute_data_api_publish(db, api, reviewer)

    if row.action == "offline_api":
        from app.services.data_service_publish import execute_data_api_offline

        api = db.query(DataApi).filter(DataApi.id == row.resource_id).first()
        if not api:
            raise HTTPException(status_code=404, detail="数据服务 API 不存在")
        return execute_data_api_offline(db, api)

    raise HTTPException(status_code=400, detail=f"未知动作: {row.action}")


def _refresh_stream_job_release_lifecycle(db: Session, job_id: int) -> None:
    from app.api.streaming import StreamingJob, StreamingJobRelease

    job = db.query(StreamingJob).filter(StreamingJob.id == job_id).first()
    if not job or getattr(job, "current_running_release_id", None):
        return
    has_approved = db.query(StreamingJobRelease.id).filter(
        StreamingJobRelease.job_id == job_id,
        StreamingJobRelease.approval_status == "approved",
    ).first()
    has_pending = db.query(StreamingJobRelease.id).filter(
        StreamingJobRelease.job_id == job_id,
        StreamingJobRelease.approval_status == "pending",
    ).first()
    job.lifecycle_state = (
        "approved" if has_approved else ("pending_approval" if has_pending else "draft")
    )
    job.updated_at = datetime.utcnow()


def approve_publish_approval(
    db: Session, user, approval_id: int, review_note: Optional[str] = None
) -> Dict[str, Any]:
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    assert_can_publish_production(db, user, row.workspace_id)
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态不可审批: {row.status}")
    _assert_approval_snapshot_fresh(db, row)

    row.review_note = (review_note or "").strip() or None
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    try:
        exec_out = _execute_approval_action(db, row, user)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"发布执行失败: {e}")

    row.status = "approved"
    if row.resource_type == "workflow":
        wf = db.query(Workflow).filter(Workflow.id == row.resource_id).first()
        if wf:
            wf.updated_by = user.id
            wf.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"approval": serialize_approval(db, row), "result": exec_out}


def reject_publish_approval(
    db: Session, user, approval_id: int, review_note: Optional[str] = None
) -> PublishApproval:
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    assert_can_publish_production(db, user, row.workspace_id)
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态不可驳回: {row.status}")
    row.status = "rejected"
    row.review_note = (review_note or "").strip() or None
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    if row.resource_type == "stream_job" and getattr(row, "release_id", None):
        from app.api.streaming import StreamingJobRelease

        release = db.query(StreamingJobRelease).filter(
            StreamingJobRelease.id == row.release_id
        ).first()
        if release and release.approval_status == "pending":
            release.approval_status = "rejected"
            release.approved_by = user.id
            release.approved_at = row.reviewed_at
            release.approval_comment = row.review_note
        _refresh_stream_job_release_lifecycle(db, row.resource_id)
    db.commit()
    db.refresh(row)
    return row


def cancel_publish_approval(db: Session, user, approval_id: int) -> PublishApproval:
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    if row.submitted_by != user.id and not workspace_data_full_control(db, user, row.workspace_id):
        raise HTTPException(status_code=403, detail="仅提交人或管理员可撤回")
    if row.status != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态不可撤回: {row.status}")
    row.status = "cancelled"
    row.reviewed_by = user.id
    row.reviewed_at = datetime.utcnow()
    if row.resource_type == "stream_job" and getattr(row, "release_id", None):
        from app.api.streaming import StreamingJobRelease

        release = db.query(StreamingJobRelease).filter(
            StreamingJobRelease.id == row.release_id
        ).first()
        if release and release.approval_status == "pending":
            release.approval_status = "cancelled"
            release.approval_comment = "发布审批已撤回"
        _refresh_stream_job_release_lifecycle(db, row.resource_id)
    db.commit()
    db.refresh(row)
    return row


def list_publish_approvals(
    db: Session,
    user,
    workspace_id: int,
    status: Optional[str] = None,
    mine_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    _assert_approval_workspace_access(db, user, workspace_id)
    q = db.query(PublishApproval).filter(PublishApproval.workspace_id == workspace_id)
    if status:
        q = q.filter(PublishApproval.status == status)
    if mine_only and not workspace_data_full_control(db, user, workspace_id):
        q = q.filter(PublishApproval.submitted_by == user.id)
    total = q.count()
    rows = (
        q.order_by(PublishApproval.submitted_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [serialize_approval(db, r) for r in rows],
        "can_review": workspace_data_full_control(db, user, workspace_id),
    }


def pending_approval_count(db: Session, workspace_id: int) -> int:
    return (
        db.query(PublishApproval)
        .filter(PublishApproval.workspace_id == workspace_id, PublishApproval.status == "pending")
        .count()
    )


def pending_resource_keys(db: Session, workspace_id: int) -> List[str]:
    rows = (
        db.query(PublishApproval)
        .filter(PublishApproval.workspace_id == workspace_id, PublishApproval.status == "pending")
        .all()
    )
    return [f"{r.resource_type}:{r.resource_id}:{r.action}" for r in rows]


def _stable_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _pretty_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def _snapshot_hash(snapshot: Any) -> Optional[str]:
    if snapshot is None:
        return None
    return hashlib.sha256(_stable_json(snapshot).encode("utf-8")).hexdigest()


def _api_fields(api: DataApi) -> Dict[str, Any]:
    params = sorted(api.params or [], key=lambda p: (p.sort_order or 0, p.id or 0))
    return {
        "name": api.name,
        "api_code": api.api_code,
        "description": api.description or "",
        "mode": api.mode,
        "http_method": api.http_method,
        "status": api.status,
        "version": api.version,
        "datasource_id": api.datasource_id,
        "sql_template": api.sql_template or "",
        "wizard_config": api.wizard_config,
        "response_fields": api.response_fields,
        "pagination_enabled": bool(api.pagination_enabled),
        "page_size_default": api.page_size_default,
        "page_size_max": api.page_size_max,
        "timeout_seconds": api.timeout_seconds,
        "cache_ttl_seconds": api.cache_ttl_seconds,
        "max_rows": api.max_rows,
        "params": [
            {
                "name": p.name,
                "param_in": p.param_in,
                "data_type": p.data_type,
                "required": bool(p.required),
                "default_value": p.default_value,
                "description": p.description,
                "validator_regex": p.validator_regex,
                "sort_order": p.sort_order,
            }
            for p in params
        ],
    }


def _api_candidate_snapshot(api: DataApi, action: str) -> Dict[str, Any]:
    baseline = _api_fields(api)
    if action == "offline_api":
        return {"action": "offline", **baseline}
    pending = api.pending_definition if isinstance(api.pending_definition, dict) else {}
    definition = pending.get("definition") if isinstance(pending.get("definition"), dict) else pending
    merged = deepcopy(baseline)
    for key, value in definition.items():
        if value is not None and key not in {"source_status", "source_version"}:
            merged[key] = deepcopy(value)
    return merged


def _workflow_snapshot(db: Session, wf: Workflow) -> Dict[str, Any]:
    from app.services.workflow_ds_publish import enrich_dag_from_db

    dag = deepcopy(wf.dag_config or {})
    enriched = enrich_dag_from_db(db, wf)
    dag["nodes"] = enriched.get("nodes") or []
    dag["edges"] = deepcopy(enriched.get("edges") or [])
    return {
        "dag": dag,
        "schedule_type": wf.schedule_type,
        "cron_expression": wf.cron_expression,
        "failure_strategy": wf.failure_strategy,
        "process_priority": wf.process_priority,
        "worker_group": wf.worker_group,
        "schedule_timezone": wf.schedule_timezone,
    }


def _stream_release_snapshot(release: Any) -> Dict[str, Any]:
    fields = (
        "job_name", "job_type", "script_content", "jar_path", "main_class",
        "program_args", "parallelism", "streaming_properties",
        "flink_sql_submit_mode", "flink_jar_submit_mode",
        "flink_session_profile_id", "jar_artifact_id", "jar_version_id",
        "connector_version_ids", "dependency_file_version_ids",
        "definition_kind", "pipeline_spec", "compiler_version",
        "generated_artifact", "spec_hash", "content_hash", "release_note",
    )
    return {
        "release_id": release.id,
        "release_version": release.version,
        **{field: deepcopy(getattr(release, field, None)) for field in fields},
    }


def _capture_approval_snapshots(
    db: Session,
    resource_type: str,
    resource: Any,
    *,
    action: str,
    release_id: Optional[int] = None,
) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    if resource_type == "studio_node":
        from app.models.workspace import NodeHistory

        submitted = {
            "script_content": resource.script_content or "",
            "node_type": resource.node_type,
            "datasource_id": resource.datasource_id,
            "params": deepcopy(resource.params or {}),
            "timeout_seconds": resource.timeout_seconds,
            "retry_times": resource.retry_times,
            "retry_interval_minutes": resource.retry_interval_minutes,
        }
        hist = (
            db.query(NodeHistory)
            .filter(NodeHistory.node_id == resource.id)
            .order_by(NodeHistory.id.desc())
            .first()
        )
        baseline = {"script_content": hist.script_content or ""} if hist else None
        return submitted, baseline

    if resource_type == "workflow":
        from app.models.workspace import JobVersion

        submitted = _workflow_snapshot(db, resource)
        base_ver = None
        if resource.active_version_id:
            base_ver = db.query(JobVersion).filter(JobVersion.id == resource.active_version_id).first()
        if not base_ver:
            base_ver = (
                db.query(JobVersion)
                .filter(JobVersion.workflow_id == resource.id, JobVersion.status == "active")
                .order_by(JobVersion.version_no.desc())
                .first()
            )
        baseline = None
        if base_ver:
            baseline = {
                "dag": deepcopy(base_ver.dag_snapshot or {}),
                "schedule_type": base_ver.schedule_type_snapshot,
                "cron_expression": base_ver.cron_snapshot,
                "version_no": base_ver.version_no,
            }
        return submitted, baseline

    if resource_type == "stream_job":
        from app.api.streaming import StreamingJobRelease

        release = db.query(StreamingJobRelease).filter(
            StreamingJobRelease.id == release_id,
            StreamingJobRelease.job_id == resource.id,
        ).first()
        if not release:
            raise HTTPException(status_code=409, detail="审批绑定的实时作业发布版本不存在")
        base_id = (
            getattr(resource, "current_running_release_id", None)
            or getattr(resource, "current_approved_release_id", None)
        )
        baseline_release = None
        if base_id and int(base_id) != int(release.id):
            baseline_release = db.query(StreamingJobRelease).filter(
                StreamingJobRelease.id == base_id,
                StreamingJobRelease.job_id == resource.id,
            ).first()
        if not baseline_release:
            baseline_release = (
                db.query(StreamingJobRelease)
                .filter(
                    StreamingJobRelease.job_id == resource.id,
                    StreamingJobRelease.approval_status == "approved",
                    StreamingJobRelease.id != release.id,
                )
                .order_by(StreamingJobRelease.version.desc())
                .first()
            )
        return (
            _stream_release_snapshot(release),
            _stream_release_snapshot(baseline_release) if baseline_release else None,
        )

    if resource_type == "data_service_api":
        baseline = _api_fields(resource)
        submitted = _api_candidate_snapshot(resource, action)
        return submitted, baseline if resource.status == "online" else None

    raise HTTPException(status_code=400, detail=f"不支持的资源类型: {resource_type}")


def _line_change_stats(original: str, modified: str) -> tuple[int, int]:
    additions = deletions = 0
    matcher = SequenceMatcher(a=original.splitlines(), b=modified.splitlines())
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op in ("replace", "delete"):
            deletions += a2 - a1
        if op in ("replace", "insert"):
            additions += b2 - b1
    return additions, deletions


def _artifact(
    key: str,
    name: str,
    kind: str,
    language: str,
    baseline: Any,
    submitted: Any,
) -> Dict[str, Any]:
    original = baseline if isinstance(baseline, str) else _pretty_json(baseline)
    modified = submitted if isinstance(submitted, str) else _pretty_json(submitted)
    additions, deletions = _line_change_stats(original, modified)
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "language": language,
        "baseline": original,
        "submitted": modified,
        "changed": original != modified,
        "additions": additions,
        "deletions": deletions,
    }


def _without_keys(value: Optional[Dict[str, Any]], *keys: str) -> Dict[str, Any]:
    return {k: deepcopy(v) for k, v in (value or {}).items() if k not in keys}


def _approval_artifacts(
    resource_type: str,
    submitted: Dict[str, Any],
    baseline: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    base = baseline or {}
    if resource_type == "studio_node":
        return [
            _artifact("script", "节点脚本", "script", _script_language(submitted.get("node_type")),
                      base.get("script_content", ""), submitted.get("script_content", "")),
        ]
    if resource_type == "workflow":
        pending_nodes = {
            str(n.get("node_id")): n for n in (submitted.get("dag") or {}).get("nodes", []) if isinstance(n, dict)
        }
        baseline_nodes = {
            str(n.get("node_id")): n for n in (base.get("dag") or {}).get("nodes", []) if isinstance(n, dict)
        }
        artifacts: List[Dict[str, Any]] = []
        for node_id in sorted(set(pending_nodes) | set(baseline_nodes)):
            pending_node = pending_nodes.get(node_id, {})
            baseline_node = baseline_nodes.get(node_id, {})
            label = pending_node.get("name") or baseline_node.get("name") or f"节点 #{node_id}"
            artifacts.append(_artifact(
                f"node:{node_id}",
                f"{label} · 脚本",
                "script",
                _script_language(pending_node.get("node_type") or baseline_node.get("node_type")),
                baseline_node.get("script_content", ""),
                pending_node.get("script_content", ""),
            ))
        pending_config = deepcopy(submitted)
        baseline_config = deepcopy(base)
        for snapshot in (pending_config, baseline_config):
            snapshot.pop("version_no", None)
            dag = snapshot.get("dag") or {}
            dag.pop("ds_meta", None)
            for node in dag.get("nodes", []):
                if isinstance(node, dict):
                    node.pop("script_content", None)
                    node.pop("ds_task_code", None)
        artifacts.append(_artifact("workflow-config", "工作流与调度配置", "json", "json", baseline_config, pending_config))
        return artifacts
    if resource_type == "stream_job":
        language = "sql" if submitted.get("job_type") == "SQL" else "plaintext"
        return [
            _artifact("script", "实时作业脚本", "script", language,
                      base.get("script_content", ""), submitted.get("script_content", "")),
            _artifact("config", "作业、制品与运行配置", "json", "json",
                      _without_keys(base, "script_content"), _without_keys(submitted, "script_content")),
        ]
    if resource_type == "data_service_api":
        return [
            _artifact("sql", "API SQL 模板", "script", "sql",
                      base.get("sql_template", ""), submitted.get("sql_template", "")),
            _artifact("config", "API、参数与响应配置", "json", "json",
                      _without_keys(base, "sql_template"), _without_keys(submitted, "sql_template")),
        ]
    return []


def _script_language(node_type: Any) -> str:
    return {"SQL": "sql", "PYTHON": "python"}.get(str(node_type or "").upper(), "plaintext")


def _assert_approval_snapshot_fresh(db: Session, row: PublishApproval) -> None:
    """乐观并发守卫：禁止批准提交后已漂移或基线已过期的 CR。"""
    if not getattr(row, "submitted_hash", None):
        return
    _, resource = _resolve_resource(db, row.workspace_id, row.resource_type, row.resource_id)
    current, baseline = _capture_approval_snapshots(
        db,
        row.resource_type,
        resource,
        action=row.action,
        release_id=getattr(row, "release_id", None),
    )
    if _snapshot_hash(current) != row.submitted_hash:
        raise HTTPException(status_code=409, detail="待发布内容在提交审批后已变更，请撤回并重新提交 CR")
    if _snapshot_hash(baseline) != getattr(row, "baseline_hash", None):
        raise HTTPException(status_code=409, detail="生产基线在审批期间已更新，请基于最新版本重新提交 CR")


def get_publish_approval_preview(db: Session, user, approval_id: int) -> Dict[str, Any]:
    """审批资源 CR 预览：始终使用提交时冻结的候选与生产基线。"""
    row = db.query(PublishApproval).filter(PublishApproval.id == approval_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="审批单不存在")
    _assert_approval_workspace_access(db, user, row.workspace_id, row.resource_type)
    _, resource = _resolve_resource(db, row.workspace_id, row.resource_type, row.resource_id)

    submitted = deepcopy(getattr(row, "submitted_snapshot", None))
    baseline = deepcopy(getattr(row, "baseline_snapshot", None))
    if submitted is None:
        submitted, baseline = _capture_approval_snapshots(
            db,
            row.resource_type,
            resource,
            action=row.action,
            release_id=getattr(row, "release_id", None),
        )
    artifacts = _approval_artifacts(row.resource_type, submitted, baseline)
    changed = [item for item in artifacts if item["changed"]]
    summary: Dict[str, Any] = {
        "name": row.resource_name,
        "changed_files": len(changed),
        "additions": sum(item["additions"] for item in changed),
        "deletions": sum(item["deletions"] for item in changed),
    }
    if row.resource_type == "studio_node":
        summary.update({
            "node_type": submitted.get("node_type"),
            "datasource_id": submitted.get("datasource_id"),
            "is_published": bool(resource.is_published),
            "is_locked": bool(resource.is_locked),
        })
    elif row.resource_type == "workflow":
        dag = submitted.get("dag") or {}
        summary.update({
            "status": resource.status,
            "schedule_type": submitted.get("schedule_type"),
            "cron_expression": submitted.get("cron_expression"),
            "node_count": len(dag.get("nodes") or []),
            "edge_count": len(dag.get("edges") or []),
        })
    elif row.resource_type == "stream_job":
        summary.update({
            "job_type": submitted.get("job_type"),
            "lifecycle_state": getattr(resource, "lifecycle_state", None),
            "release_version": submitted.get("release_version"),
        })
    elif row.resource_type == "data_service_api":
        summary.update({
            "api_code": submitted.get("api_code"),
            "mode": submitted.get("mode"),
            "status": resource.status,
            "version": resource.version,
        })

    baseline_label = {
        "studio_node": "最近保存版本",
        "workflow": f"生产版本 v{baseline.get('version_no')}" if baseline else "首次发布",
        "stream_job": f"生产运行版本 v{baseline.get('release_version')}" if baseline else "首次发布",
        "data_service_api": "当前线上版本" if baseline else "首次发布",
    }.get(row.resource_type)
    return {
        "approval": serialize_approval(db, row),
        "preview": {
            "kind": row.resource_type,
            "action": row.action,
            "summary": summary,
            "pending": submitted,
            "baseline": baseline,
            "baseline_label": baseline_label,
            "has_diff": bool(changed),
            "artifacts": artifacts,
            "snapshot_frozen": getattr(row, "submitted_snapshot", None) is not None,
        },
    }

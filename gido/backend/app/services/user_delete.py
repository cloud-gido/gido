# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""平台用户删除：先解除外键引用，避免 IntegrityError → 500。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.data_service import ConsumerApp, DataApi
from app.models.workspace import (
    AuditLog,
    DataSource,
    NodeHistory,
    PublishApproval,
    TaskNode,
    User,
    Workflow,
    Workspace,
    WorkspaceMember,
    WorkspaceVariable,
)


def _null(db: Session, model, column, user_id: int) -> None:
    db.query(model).filter(column == user_id).update({column: None}, synchronize_session=False)


def _reassign(db: Session, model, column, user_id: int, to_user_id: int) -> None:
    db.query(model).filter(column == user_id).update({column: to_user_id}, synchronize_session=False)


def detach_user_references(db: Session, user_id: int, *, reassign_to: int) -> None:
    """删除用户前清理/改挂业务引用。"""
    owned = (
        db.query(Workspace.id, Workspace.name)
        .filter(Workspace.owner_id == user_id)
        .order_by(Workspace.id.asc())
        .all()
    )
    if owned:
        names = "、".join(n for _, n in owned[:8])
        more = f" 等{len(owned)}个" if len(owned) > 8 else ""
        raise HTTPException(
            status_code=400,
            detail=f"用户仍是工作空间负责人，请先转让后再删除：{names}{more}",
        )

    db.query(WorkspaceMember).filter(WorkspaceMember.user_id == user_id).delete(synchronize_session=False)

    db.query(TaskNode).filter(TaskNode.edit_lock_user_id == user_id).update(
        {TaskNode.edit_lock_user_id: None, TaskNode.edit_lock_at: None},
        synchronize_session=False,
    )

    # 可空 → NULL
    for model, col in (
        (TaskNode, TaskNode.owner_id),
        (DataApi, DataApi.owner_id),
        (DataApi, DataApi.published_by),
        (DataApi, DataApi.created_by),
        (ConsumerApp, ConsumerApp.created_by),
        (PublishApproval, PublishApproval.reviewed_by),
        (WorkspaceVariable, WorkspaceVariable.created_by),
        (Workflow, Workflow.updated_by),
    ):
        _null(db, model, col, user_id)

    # 不可空或需保留历史 → 改挂到操作者
    for model, col in (
        (AuditLog, AuditLog.user_id),
        (PublishApproval, PublishApproval.submitted_by),
        (DataSource, DataSource.created_by),
        (TaskNode, TaskNode.created_by),
        (NodeHistory, NodeHistory.saved_by),
        (Workflow, Workflow.created_by),
    ):
        _reassign(db, model, col, user_id, reassign_to)

    # 其它 workspace 模型上常见 user 列（存在才处理）
    for model_name, col_name, mode in (
        ("JobVersion", "published_by", "null"),
        ("WorkflowInstance", "triggered_by", "null"),
        ("QualityRule", "created_by", "reassign"),
        ("SyncTask", "created_by", "reassign"),
        ("AlertEvent", "assignee_id", "null"),
        ("AlertEvent", "ack_by", "null"),
        ("AlertNotificationConfig", "updated_by", "null"),
        ("BackfillRequest", "created_by", "null"),
    ):
        model = getattr(__import__("app.models.workspace", fromlist=[model_name]), model_name, None)
        if model is None:
            continue
        col = getattr(model, col_name, None)
        if col is None:
            continue
        if mode == "null":
            _null(db, model, col, user_id)
        else:
            _reassign(db, model, col, user_id, reassign_to)

    # Stream / pipeline
    try:
        from app.api.stream_pipeline import (
            StreamConnectionProfile,
            StreamDeploymentGroup,
            StreamSchemaContract,
            StreamSchemaEvolutionAudit,
            StreamSchemaVersion,
        )

        for model in (
            StreamConnectionProfile,
            StreamSchemaContract,
            StreamSchemaVersion,
            StreamSchemaEvolutionAudit,
            StreamDeploymentGroup,
        ):
            col = getattr(model, "created_by", None)
            if col is not None:
                _null(db, model, col, user_id)
    except Exception:
        pass

    try:
        from app.api.streaming import (
            StreamingJob,
            StreamingJobHistory,
            StreamingJobRelease,
            StreamingOperation,
            StreamingRestorePoint,
        )

        for model, cols in (
            (StreamingJob, ("owner_id", "created_by", "last_submitted_by")),
            (StreamingJobHistory, ("saved_by",)),
            (StreamingJobRelease, ("submitted_by", "approved_by")),
            (StreamingRestorePoint, ("created_by",)),
            (StreamingOperation, ("requested_by",)),
        ):
            for col_name in cols:
                col = getattr(model, col_name, None)
                if col is None:
                    continue
                if col_name in ("created_by",) and model is StreamingJob:
                    _reassign(db, model, col, user_id, reassign_to)
                else:
                    _null(db, model, col, user_id)

        # 制品库
        for model_name in (
            "JarArtifact",
            "JarArtifactVersion",
            "ConnectorArtifact",
            "ConnectorArtifactVersion",
            "FileArtifact",
            "FileArtifactVersion",
            "FlinkSessionProfile",
        ):
            model = getattr(
                __import__("app.api.streaming", fromlist=[model_name]), model_name, None
            )
            if model is None:
                try:
                    model = getattr(
                        __import__("app.api.streaming_resource_library", fromlist=[model_name]),
                        model_name,
                        None,
                    )
                except Exception:
                    model = None
            if model is None:
                continue
            for col_name in ("owner_id", "created_by", "uploaded_by"):
                col = getattr(model, col_name, None)
                if col is not None:
                    _null(db, model, col, user_id)
    except Exception:
        pass

    try:
        from app.api.stream_pipeline import StreamPipelineSloPolicy

        col = getattr(StreamPipelineSloPolicy, "updated_by", None)
        if col is not None:
            _null(db, StreamPipelineSloPolicy, col, user_id)
    except Exception:
        pass

    try:
        from app.models.workspace import AdhocRun

        col = getattr(AdhocRun, "triggered_by", None)
        if col is not None:
            _null(db, AdhocRun, col, user_id)
    except Exception:
        pass


def delete_platform_user(db: Session, user: User, *, actor: User) -> None:
    detach_user_references(db, user.id, reassign_to=actor.id)
    avatar = getattr(user, "avatar", None)
    try:
        db.delete(user)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="用户仍被业务数据引用，无法删除。可先禁用账号，或转让工作空间负责人后再试。",
        ) from e

    if avatar and str(avatar).startswith("upload:"):
        try:
            from app.services.user_avatar import delete_uploaded_avatar

            delete_uploaded_avatar(str(avatar))
        except Exception:
            pass

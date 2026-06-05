# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""RBAC 权限检查"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.workspace import WorkspaceMember, Workspace

from app.core.access import user_has_any, is_platform_manager_role, assert_any_permission
from app.core import perm_codes as PC

ROLE_WEIGHTS = {"viewer": 1, "developer": 2, "admin": 3}
VALID_SPACE_MEMBER_ROLES = frozenset(ROLE_WEIGHTS)


def assert_can_manage_workspace_members(db: Session, user, workspace_id: int) -> None:
    """
    管理某工作空间的成员：平台管理员（含内置 platform_admin 角色）、workspace:member:manage、
    或该空间内空间角色 admin（负责人视为 admin）。
    """
    if is_platform_manager_role(db, user):
        return
    if user_has_any(db, user, [PC.WORKSPACE_MEMBER_MANAGE]):
        ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if not ws:
            raise HTTPException(status_code=404, detail="工作空间不存在")
        return
    check_workspace_permission(db, user, workspace_id, "admin")


def assert_can_edit_workspace_metadata(db: Session, user, workspace_id: int) -> None:
    """修改空间名称/描述/时区：平台管理员，或该空间的空间管理员（成员角色 admin）。"""
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="工作空间不存在")
    if is_platform_manager_role(db, user):
        return
    check_workspace_permission(db, user, workspace_id, "admin")


def check_workspace_permission(db: Session, user, workspace_id: int, min_role: str = "viewer"):
    """检查用户对工作空间的权限，不足则抛 403"""
    if is_platform_manager_role(db, user):
        return
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if ws and ws.owner_id == user.id:
        return
    member = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user.id
    ).first()
    if not member:
        raise HTTPException(status_code=403, detail="无工作空间访问权限")
    if ROLE_WEIGHTS.get(member.role, 0) < ROLE_WEIGHTS.get(min_role, 0):
        raise HTTPException(status_code=403, detail=f"需要 {min_role} 及以上权限")


def get_user_role(db: Session, user, workspace_id: int) -> str:
    """
    当前用户在某工作空间内的**空间角色**（与平台 `dw_roles` 无关）。
    负责人视为 admin；仅成员表内有记录时返回 viewer/developer/admin；否则为 none。
    平台管理员在数据面可代管任一空间（此处返回 admin 便于 UI 展示）。
    """
    if is_platform_manager_role(db, user):
        return "admin"
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        return "none"
    if ws.owner_id == user.id:
        return "admin"
    member = db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user.id
    ).first()
    return member.role if member else "none"


def assert_workspace_access(db: Session, user, workspace_id: int) -> None:
    """至少能进入该空间（与 check_workspace_permission(..., viewer) 等价，命名强调「身份绑定」）。"""
    check_workspace_permission(db, user, workspace_id, "viewer")


def workspace_data_full_control(db: Session, user, workspace_id: int) -> bool:
    """
    True：平台管理员 或 本空间的空间管理员(admin/负责人)。
    在此空间内进行开发/数据源/运维等业务时等价于 historic「单租户超管」（不含 platform system:*）。
    """
    return is_platform_manager_role(db, user) or get_user_role(db, user, workspace_id) == "admin"


def assert_workspace_data_capability(
    db: Session,
    user,
    workspace_id: int,
    min_member_role_when_not_full: str,
    *platform_fallback_codes: str,
) -> None:
    """
    对绑定到 workspace_id 的业务接口：
    — 若有空间全权：仅需能进入空间（viewer 门槛）。
    — 否则按成员下限 + 平台 RBAC 细码兜底（developer 需平台 write 码等）。
    """
    if workspace_data_full_control(db, user, workspace_id):
        assert_workspace_access(db, user, workspace_id)
        return
    check_workspace_permission(db, user, workspace_id, min_member_role_when_not_full)
    assert_any_permission(db, user, *platform_fallback_codes)


def assert_can_list_workspaces(db: Session, user) -> None:
    """GET /workspaces：平台管理员、具备 workspace:read，或任一可访问的成员空间即可列出。"""
    if is_platform_manager_role(db, user):
        return
    if user_has_any(db, user, [PC.WORKSPACE_READ]):
        return
    if get_accessible_workspace_ids(db, user):
        return
    raise HTTPException(status_code=403, detail="无权查看工作空间列表")


def assert_gido_stream_infra_probe_access(db: Session, user) -> None:
    """Flink 集群概览/连通性自检：平台管理员、具备 gido:stream:read，或掌管至少一个空间的空间管理员。"""
    if is_platform_manager_role(db, user):
        return
    if user_has_any(db, user, [PC.GIDO_STREAM_READ]):
        return
    if workspace_ids_where_user_space_admin(db, user):
        return
    raise HTTPException(status_code=403, detail="无权查看实时计算集群信息")


def workspace_ids_where_user_space_admin(db: Session, user) -> list[int]:
    """成员角色 admin 或为 owner 的空间 id。"""
    owned = [r[0] for r in db.query(Workspace.id).filter(Workspace.owner_id == user.id).all()]
    member_admin = [
        r[0]
        for r in db.query(WorkspaceMember.workspace_id).filter(
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.role == "admin",
        ).all()
    ]
    return list(dict.fromkeys(owned + member_admin))


def get_accessible_workspace_ids(db: Session, user) -> list[int]:
    """负责人、成员所属空间 id；平台管理员（含内置 platform_admin 角色）视为可访问全部空间。"""
    if is_platform_manager_role(db, user):
        return [r[0] for r in db.query(Workspace.id).order_by(Workspace.id).all()]
    owned = [r[0] for r in db.query(Workspace.id).filter(Workspace.owner_id == user.id).all()]
    member = [
        r[0]
        for r in db.query(WorkspaceMember.workspace_id).filter(
            WorkspaceMember.user_id == user.id
        ).all()
    ]
    return list(dict.fromkeys(owned + member))


# ----- 资源级：从主键解析 workspace_id 后再校验，防跨空间 IDOR -----


def require_workflow(
    db: Session,
    user,
    wf_id: int,
    min_member_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.models.workspace import Workflow
    from app.core import perm_codes as PCM

    wf = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    code = fallback_code or PCM.GIDO_BATCH_WORKFLOW_READ
    effective_min = (
        "developer" if min_member_role == "viewer" and code == PCM.GIDO_BATCH_WORKFLOW_READ else min_member_role
    )
    assert_workspace_data_capability(db, user, wf.workspace_id, effective_min, code)
    return wf


def require_node_instance(
    db: Session,
    user,
    ni_id: int,
    min_member_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.models.workspace import NodeInstance, TaskNode

    from app.core import perm_codes as PCM

    ni = db.query(NodeInstance).filter(NodeInstance.id == ni_id).first()
    if not ni:
        raise HTTPException(status_code=404, detail="节点实例不存在")
    node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    code = fallback_code or PCM.GIDO_BATCH_OPERATION_READ
    effective_min = (
        "developer" if min_member_role == "viewer" and code == PCM.GIDO_BATCH_OPERATION_READ else min_member_role
    )
    assert_workspace_data_capability(db, user, node.workspace_id, effective_min, code)
    return ni


def require_streaming_job(
    db: Session,
    user,
    job_id: int,
    min_member_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.api.streaming import StreamingJob

    from app.core import perm_codes as PCM

    job = db.query(StreamingJob).filter(StreamingJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    code = fallback_code or PCM.GIDO_STREAM_READ
    effective_min = (
        "developer" if min_member_role == "viewer" and code == PCM.GIDO_STREAM_READ else min_member_role
    )
    assert_workspace_data_capability(db, user, job.workspace_id, effective_min, code)
    return job


def require_meta_table(
    db: Session,
    user,
    table_id: int,
    min_member_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.models.workspace import MetaTable

    from app.core import perm_codes as PCM

    t = db.query(MetaTable).filter(MetaTable.id == table_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="表不存在")
    code = fallback_code or PCM.GIDO_BATCH_DATAMAP_READ
    assert_workspace_data_capability(db, user, t.workspace_id, min_member_role, code)
    return t


def require_quality_rule(
    db: Session,
    user,
    rule_id: int,
    min_member_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.models.workspace import QualityRule

    from app.core import perm_codes as PCM

    r = db.query(QualityRule).filter(QualityRule.id == rule_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="规则不存在")
    code = fallback_code or PCM.GIDO_BATCH_QUALITY_READ
    effective_min = (
        "developer" if min_member_role == "viewer" and code == PCM.GIDO_BATCH_QUALITY_READ else min_member_role
    )
    assert_workspace_data_capability(db, user, r.workspace_id, effective_min, code)
    return r


def require_datasource_row(db: Session, user, ds_id: int):
    from app.models.workspace import DataSource

    from app.core import perm_codes as PCM

    ds = db.query(DataSource).filter(DataSource.id == ds_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    assert_workspace_data_capability(db, user, ds.workspace_id, "viewer", PCM.GIDO_BATCH_DATASOURCE_READ)
    return ds


def require_task_node(
    db: Session,
    user,
    node_id: int,
    min_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.models.workspace import TaskNode
    from app.core import perm_codes as PCM

    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    fc = fallback_code if fallback_code is not None else PCM.GIDO_BATCH_STUDIO_READ
    # 读节点详情属开发域，空间只读(viewer)不可访问（探查/字典走独立 API）
    effective_min = "developer" if min_role == "viewer" and fc == PCM.GIDO_BATCH_STUDIO_READ else min_role
    assert_workspace_data_capability(db, user, node.workspace_id, effective_min, fc)
    return node


def require_sync_task(
    db: Session,
    user,
    task_id: int,
    min_member_role: str = "viewer",
    fallback_code: Optional[str] = None,
):
    from app.models.workspace import SyncTask

    from app.core import perm_codes as PCM

    task = db.query(SyncTask).filter(SyncTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    code = fallback_code or PCM.GIDO_BATCH_INTEGRATION_READ
    effective_min = (
        "developer" if min_member_role == "viewer" and code == PCM.GIDO_BATCH_INTEGRATION_READ else min_member_role
    )
    assert_workspace_data_capability(db, user, task.workspace_id, effective_min, code)
    return task

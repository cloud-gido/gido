# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session, load_only
from pydantic import BaseModel, field_validator
from typing import Optional, Any, Dict, List
from datetime import datetime
import json
import ast
from sqlalchemy import func
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import TaskNode, NodeDependency, NodeInstance, NodeFolder, User
from app.services.rbac import (
    assert_workspace_data_capability,
    require_task_node,
    require_datasource_row,
    workspace_data_full_control,
)
from app.services.audit import log_action
from app.core.config import settings
from app.services.publish_approval import assert_can_publish_production
from app.services.python_job_runner import run_python_node

# 协作编辑锁过期时间（秒），过期后他人可直接占用或抢锁
EDIT_LOCK_TTL_SECONDS = 30 * 60

# 列表接口默认不带 script_content（侧栏/工作流选节点不需要全文）；打开编辑再用 GET /nodes/{id}
_TASK_NODE_LIST_LOAD_COLS = (
    TaskNode.id,
    TaskNode.workspace_id,
    TaskNode.name,
    TaskNode.node_type,
    TaskNode.datasource_id,
    TaskNode.params,
    TaskNode.folder_id,
    TaskNode.sort_order,
    TaskNode.timeout_seconds,
    TaskNode.retry_times,
    TaskNode.is_published,
    TaskNode.owner_id,
    TaskNode.is_locked,
    TaskNode.edit_lock_user_id,
    TaskNode.edit_lock_at,
    TaskNode.created_at,
    TaskNode.updated_at,
    TaskNode.created_by,
)

router = APIRouter(prefix="/studio", tags=["数据开发"])


def _username_by_id(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    return u.username if u else None


def _username_map(db: Session, user_ids: List[Optional[int]]) -> Dict[int, str]:
    """批量解析用户名，避免列表接口 N+1。"""
    clean = {int(i) for i in user_ids if i is not None}
    if not clean:
        return {}
    return {u.id: u.username for u in db.query(User).filter(User.id.in_(list(clean))).all()}


def _edit_lock_expired(node: TaskNode) -> bool:
    if not getattr(node, "edit_lock_user_id", None) or not getattr(node, "edit_lock_at", None):
        return True
    at = node.edit_lock_at
    if at is None:
        return True
    return (datetime.utcnow() - at).total_seconds() > EDIT_LOCK_TTL_SECONDS


def _effective_edit_lock_for_api(
    db: Session,
    node: TaskNode,
    *,
    username_by_id: Optional[Dict[int, str]] = None,
):
    """返回当前有效的编辑锁（过期则视为无锁，仅展示用；持久清理在 acquire/update）。"""
    uid = getattr(node, "edit_lock_user_id", None)
    at = getattr(node, "edit_lock_at", None)
    if not uid or not at:
        return None, None, None
    if _edit_lock_expired(node):
        return None, None, None
    if username_by_id is not None:
        uname = username_by_id.get(int(uid))
    else:
        uname = _username_by_id(db, uid)
    return uid, uname, at.isoformat() if hasattr(at, "isoformat") else None


def _persist_clear_expired_edit_lock(db: Session, node: TaskNode) -> None:
    if getattr(node, "edit_lock_user_id", None) and _edit_lock_expired(node):
        node.edit_lock_user_id = None
        node.edit_lock_at = None


def _serialize_task_node(
    db: Session,
    node: TaskNode,
    *,
    include_script: bool = True,
    username_by_id: Optional[Dict[int, str]] = None,
) -> dict:
    lock_uid, lock_uname, lock_at_s = _effective_edit_lock_for_api(
        db, node, username_by_id=username_by_id
    )
    owner_id = node.owner_id
    creator_id = node.created_by
    if username_by_id is not None:
        owner_uname = username_by_id.get(int(owner_id)) if owner_id else None
        creator_uname = username_by_id.get(int(creator_id)) if creator_id else None
    else:
        owner_uname = _username_by_id(db, owner_id)
        creator_uname = _username_by_id(db, creator_id)
    out = {
        "id": node.id,
        "workspace_id": node.workspace_id,
        "name": node.name,
        "node_type": node.node_type,
        "datasource_id": node.datasource_id,
        "folder_id": node.folder_id,
        "sort_order": getattr(node, "sort_order", 0) or 0,
        "timeout_seconds": node.timeout_seconds,
        "retry_times": node.retry_times,
        "params": node.params,
        "is_published": bool(node.is_published),
        "owner_id": owner_id,
        "created_by": creator_id,
        "creator_username": creator_uname,
        "owner_username": owner_uname or creator_uname,
        "is_locked": bool(getattr(node, "is_locked", False)),
        "edit_lock_user_id": lock_uid,
        "edit_lock_username": lock_uname,
        "edit_lock_at": lock_at_s,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
    }
    if include_script:
        out["script_content"] = node.script_content
    else:
        # 明示缺省，避免前端把 undefined 当「空脚本」误覆盖本地草稿
        out["script_content"] = None
    return out


# ==================== 文件夹 ====================

class FolderCreate(BaseModel):
    workspace_id: int
    name: str
    parent_id: Optional[int] = None
    scope: str = "batch"


@router.get("/folders")
def list_folders(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_STUDIO_READ)
    folders = (
        db.query(NodeFolder)
        .filter(NodeFolder.workspace_id == workspace_id, NodeFolder.scope == "batch")
        .order_by(NodeFolder.sort_order.asc(), NodeFolder.name.asc(), NodeFolder.id.asc())
        .all()
    )
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "scope": getattr(f, "scope", None) or "batch",
            "sort_order": getattr(f, "sort_order", 0) or 0,
        }
        for f in folders
    ]


@router.post("/folders")
def create_folder(folder_in: FolderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, folder_in.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    scope = (folder_in.scope or "batch").strip().lower()
    if scope != "batch":
        raise HTTPException(status_code=400, detail="数据开发目录 scope 须为 batch")
    if folder_in.parent_id is not None:
        parent = db.query(NodeFolder).filter(NodeFolder.id == folder_in.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="父文件夹不存在")
        if parent.workspace_id != folder_in.workspace_id:
            raise HTTPException(status_code=400, detail="父文件夹与工作空间不一致")
        if (getattr(parent, "scope", None) or "batch") != "batch":
            raise HTTPException(status_code=400, detail="父文件夹不属于数据开发目录树")
    from app.services.tree_sort import sort_order_for_new_folder_peer

    folder = NodeFolder(
        workspace_id=folder_in.workspace_id,
        name=folder_in.name,
        parent_id=folder_in.parent_id,
        scope="batch",
        sort_order=sort_order_for_new_folder_peer(
            db,
            workspace_id=folder_in.workspace_id,
            parent_id=folder_in.parent_id,
            scope="batch",
            folder_model=NodeFolder,
        ),
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


class FolderReorderIn(BaseModel):
    workspace_id: int
    parent_id: Optional[int] = None
    folder_ids: List[int]


# 静态路径须在 /folders/{folder_id} 之前注册，否则 "reorder" 会被当成 folder_id
@router.put("/folders/reorder")
def reorder_folders(
    body: FolderReorderIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """同级目录内拖拽排序（与脚本 reorder 语义一致）。"""
    from app.services.node_folders import folder_scope

    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if not body.folder_ids:
        return {"ok": True}
    folders = (
        db.query(NodeFolder)
        .filter(NodeFolder.workspace_id == body.workspace_id, NodeFolder.id.in_(body.folder_ids))
        .all()
    )
    if len(folders) != len(set(body.folder_ids)):
        raise HTTPException(status_code=400, detail="存在无效目录 ID")
    parent_id = body.parent_id
    by_id = {f.id: f for f in folders}
    for fid in body.folder_ids:
        f = by_id[fid]
        if folder_scope(f) != "batch":
            raise HTTPException(status_code=400, detail="目录不属于数据开发目录树")
        fp = f.parent_id if f.parent_id is not None else None
        if fp != parent_id:
            raise HTTPException(status_code=400, detail="目录不在同一父级下，无法排序")
    for i, fid in enumerate(body.folder_ids):
        by_id[fid].sort_order = (i + 1) * 10
    db.commit()
    return {"ok": True}


@router.put("/folders/{folder_id}")
def rename_folder(folder_id: int, name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    folder.name = name
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}


class FolderParentPatch(BaseModel):
    parent_id: Optional[int] = None


@router.patch("/folders/{folder_id}/parent")
def move_folder_parent(
    folder_id: int,
    body: FolderParentPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """整目录挪动：修改 parent_id（含子树）。不影响工作流调度（仅组织用）。"""
    from app.services.node_folders import folder_scope, reparent_folder

    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder or folder_scope(folder) != "batch":
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    reparent_folder(db, folder, body.parent_id, expected_scope="batch")
    db.commit()
    db.refresh(folder)
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "scope": "batch",
        "sort_order": getattr(folder, "sort_order", 0) or 0,
    }


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    kids = db.query(NodeFolder).filter(NodeFolder.parent_id == folder_id).count()
    if kids:
        raise HTTPException(status_code=400, detail="请先删除或移走子目录")
    # 将文件夹内节点移到根目录
    db.query(TaskNode).filter(TaskNode.folder_id == folder_id).update({"folder_id": None})
    db.delete(folder)
    db.commit()
    return {"message": "删除成功"}


class NodeCreate(BaseModel):
    workspace_id: int
    name: str
    node_type: str
    script_content: Optional[str] = None
    datasource_id: Optional[int] = None
    folder_id: Optional[int] = None
    timeout_seconds: Optional[int] = 3600
    retry_times: Optional[int] = 0
    params: Optional[Dict[str, Any]] = None  # 自定义变量，如 {"env": "prod"}

    @field_validator("params", mode="before")
    @classmethod
    def coerce_params(cls, v: Any) -> Optional[Dict[str, Any]]:
        """表单常把空串或 JSON 字符串传来；空串须视为清空，否则 Pydantic 校验失败导致「保存不下来」。"""
        if v is None or v == "":
            return None
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(s)
                except (ValueError, SyntaxError):
                    raise ValueError(
                        '自定义变量须为 JSON 对象（键用双引号）或 Python 字面量，例如 {"xx":"yy"} 或 {\'xx\':\'yy\'}'
                    )
            if parsed is None:
                return None
            if not isinstance(parsed, dict):
                raise ValueError("自定义变量必须是「键值对对象」，不能是数组或单个字符串")
            return parsed
        raise ValueError("自定义变量 params 格式无效")


def _next_sort_order(db: Session, workspace_id: int, folder_id: Optional[int]) -> int:
    """新建/移入时的 sort_order：未手动排过则 0（展示字典序），否则追加末尾。"""
    from app.services.tree_sort import sort_order_for_new_peer

    return sort_order_for_new_peer(db, TaskNode, workspace_id, folder_id)


@router.get("/nodes")
def list_nodes(
    workspace_id: int,
    folder_id: Optional[int] = None,
    include_script: bool = Query(
        False,
        description="是否返回 script_content；侧栏列表默认 false，打开脚本请用 GET /nodes/{id}",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_STUDIO_READ)
    q = db.query(TaskNode).filter(TaskNode.workspace_id == workspace_id)
    if folder_id is not None:
        q = q.filter(TaskNode.folder_id == folder_id)
    if not include_script:
        q = q.options(load_only(*_TASK_NODE_LIST_LOAD_COLS))
    nodes = q.order_by(TaskNode.sort_order.asc(), TaskNode.name.asc(), TaskNode.id.asc()).all()
    uids: List[Optional[int]] = []
    for n in nodes:
        uids.append(n.owner_id)
        uids.append(n.created_by)
        if not _edit_lock_expired(n):
            uids.append(getattr(n, "edit_lock_user_id", None))
    umap = _username_map(db, uids)
    return [
        _serialize_task_node(db, n, include_script=include_script, username_by_id=umap)
        for n in nodes
    ]


@router.post("/nodes")
def create_node(node_in: NodeCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, node_in.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if node_in.datasource_id is not None:
        ds = require_datasource_row(db, current_user, node_in.datasource_id)
        if ds.workspace_id != node_in.workspace_id:
            raise HTTPException(status_code=400, detail="数据源不属于该工作空间")
    payload = node_in.model_dump()
    if (payload.get("node_type") or "").upper() == "DEPENDENT":
        from app.services.workflow_dependent import normalize_dependent_params
        payload["params"] = normalize_dependent_params(payload.get("params"))
        if not (payload.get("script_content") or "").strip():
            payload["script_content"] = "# DEPENDENT：等待其他工作流成功（无脚本）\n"
    node = TaskNode(
        **payload,
        sort_order=_next_sort_order(db, node_in.workspace_id, node_in.folder_id),
        created_by=current_user.id,
        owner_id=current_user.id,
        is_locked=False,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    log_action(db, current_user.id, "create", "node", node.id, node.name, node_in.workspace_id)
    return _serialize_task_node(db, node)


@router.get("/nodes/{node_id}")
def get_node(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_READ)
    return _serialize_task_node(db, node)


class NodeFolderPatch(BaseModel):
    folder_id: Optional[int] = None


@router.patch("/nodes/{node_id}/folder")
def move_node_to_folder(
    node_id: int,
    body: NodeFolderPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """仅调整节点所在目录（不参与脚本协作锁，便于拖拽整理）。"""
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    folder_id = body.folder_id
    if folder_id is not None:
        fo = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
        if not fo or fo.workspace_id != node.workspace_id:
            raise HTTPException(status_code=400, detail="目标文件夹不存在或不属于该工作空间")
        if (getattr(fo, "scope", None) or "batch") != "batch":
            raise HTTPException(status_code=400, detail="目标文件夹不属于数据开发目录树")
    node.folder_id = folder_id
    node.sort_order = _next_sort_order(db, node.workspace_id, folder_id)
    node.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(node)
    return _serialize_task_node(db, node)


class NodeReorderIn(BaseModel):
    workspace_id: int
    folder_id: Optional[int] = None
    node_ids: List[int]


@router.put("/nodes/reorder")
def reorder_nodes(
    body: NodeReorderIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """同目录内调整节点顺序（拖拽排序）。"""
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if not body.node_ids:
        return {"ok": True}
    nodes = (
        db.query(TaskNode)
        .filter(TaskNode.workspace_id == body.workspace_id, TaskNode.id.in_(body.node_ids))
        .all()
    )
    if len(nodes) != len(set(body.node_ids)):
        raise HTTPException(status_code=400, detail="存在无效节点 ID")
    folder_id = body.folder_id
    for n in nodes:
        nf = n.folder_id if n.folder_id is not None else None
        if nf != folder_id:
            raise HTTPException(status_code=400, detail="节点与目标目录不一致，请先移动到同一目录")
    for i, nid in enumerate(body.node_ids):
        node = next(n for n in nodes if n.id == nid)
        node.sort_order = (i + 1) * 10
    db.commit()
    return {"ok": True}


@router.put("/nodes/{node_id}")
def update_node(
    node_id: int,
    node_in: NodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    create_history: bool = Query(
        True,
        description="是否写入版本历史。编辑器自动草稿保存应传 false（草稿≠提交版本）。",
    ),
):
    from app.models.workspace import NodeHistory
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if getattr(node, "is_locked", False):
        raise HTTPException(
            status_code=403,
            detail="脚本已提交并锁定（已纳入 GIDO 发布治理）。请由负责人或空间管理员解锁后再修改。",
        )
    _persist_clear_expired_edit_lock(db, node)
    if getattr(node, "edit_lock_user_id", None) and node.edit_lock_user_id != current_user.id:
        hu = _username_by_id(db, node.edit_lock_user_id)
        raise HTTPException(
            status_code=403,
            detail=f"编辑锁由「{hu or node.edit_lock_user_id}」占用，请先在左侧打开脚本并获取编辑锁，或使用抢锁。",
        )
    patch = node_in.model_dump(exclude_unset=True)
    patch.pop("workspace_id", None)
    patch.pop("sort_order", None)
    if "datasource_id" in patch and patch.get("datasource_id") is not None:
        ds = require_datasource_row(db, current_user, patch["datasource_id"])
        if ds.workspace_id != node.workspace_id:
            raise HTTPException(status_code=400, detail="数据源不属于该节点所在工作空间")
    eff_type = (patch.get("node_type") or node.node_type or "").upper()
    if eff_type == "DEPENDENT" and ("params" in patch or (node.node_type or "").upper() == "DEPENDENT"):
        from app.services.workflow_dependent import normalize_dependent_params
        base_params = node.params if isinstance(node.params, dict) else {}
        merged = {**base_params, **(patch.get("params") or {})}
        patch["params"] = normalize_dependent_params(merged)

    # 显式保存：将「变更前」脚本写入版本历史；自动草稿保存不写历史
    if create_history and "script_content" in patch:
        old_script = node.script_content or ""
        new_script = patch.get("script_content") or ""
        if old_script and old_script != new_script:
            db.add(NodeHistory(node_id=node_id, script_content=old_script, saved_by=current_user.id))

    if not patch:
        return _serialize_task_node(db, node)

    # 仅脚本且内容未变：跳过写库（自动保存常见）
    if set(patch.keys()) == {"script_content"} and (patch.get("script_content") or "") == (node.script_content or ""):
        return _serialize_task_node(db, node)

    for k, v in patch.items():
        setattr(node, k, v)
    node.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(node)
    return _serialize_task_node(db, node)


@router.delete("/nodes/{node_id}")
def delete_node(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.models.workspace import AdhocRun, AlertEvent, Lineage, NodeHistory

    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if getattr(node, "is_locked", False) and not workspace_data_full_control(db, current_user, node.workspace_id):
        raise HTTPException(status_code=403, detail="节点已锁定，仅空间管理员或平台管理员可删除")

    # 先清外键引用，再删节点（历史 / 依赖 / 实例 / 试跑 / 告警 / 血缘）
    inst_ids = [
        r[0]
        for r in db.query(NodeInstance.id).filter(NodeInstance.node_id == node_id).all()
    ]
    if inst_ids:
        db.query(AdhocRun).filter(AdhocRun.node_instance_id.in_(inst_ids)).update(
            {AdhocRun.node_instance_id: None}, synchronize_session=False
        )
        db.query(AlertEvent).filter(AlertEvent.node_instance_id.in_(inst_ids)).update(
            {AlertEvent.node_instance_id: None}, synchronize_session=False
        )
    db.query(AdhocRun).filter(AdhocRun.node_id == node_id).update(
        {AdhocRun.node_id: None}, synchronize_session=False
    )
    db.query(NodeInstance).filter(NodeInstance.node_id == node_id).delete(synchronize_session=False)
    db.query(NodeHistory).filter(NodeHistory.node_id == node_id).delete(synchronize_session=False)
    db.query(NodeDependency).filter(
        (NodeDependency.node_id == node_id) | (NodeDependency.depends_on_id == node_id)
    ).delete(synchronize_session=False)
    db.query(Lineage).filter(Lineage.task_node_id == node_id).update(
        {Lineage.task_node_id: None}, synchronize_session=False
    )

    db.delete(node)
    db.commit()
    return {"message": "删除成功"}


@router.post("/nodes/{node_id}/unlock")
def unlock_node(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """负责人或空间管理员解锁后可再次编辑脚本。"""
    node = require_task_node(db, current_user, node_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    oid = node.owner_id or node.created_by
    if current_user.id != oid and not workspace_data_full_control(db, current_user, node.workspace_id):
        raise HTTPException(status_code=403, detail="仅脚本负责人或空间管理员可解锁")
    node.is_locked = False
    node.edit_lock_user_id = None
    node.edit_lock_at = None
    node.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "已解锁", "node": _serialize_task_node(db, node)}


@router.post("/nodes/{node_id}/acquire-edit-lock")
def acquire_edit_lock(
    node_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """占用协作编辑锁（他人占用时可 force 抢锁）。与发布锁定 is_locked 独立。"""
    node = require_task_node(db, current_user, node_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if getattr(node, "is_locked", False):
        raise HTTPException(status_code=400, detail="脚本已发布锁定，请先解锁后再占用编辑锁")
    _persist_clear_expired_edit_lock(db, node)
    db.flush()
    if not getattr(node, "edit_lock_user_id", None):
        node.edit_lock_user_id = current_user.id
        node.edit_lock_at = datetime.utcnow()
        db.commit()
        db.refresh(node)
        return {"message": "已获取编辑锁", "node": _serialize_task_node(db, node)}
    if node.edit_lock_user_id == current_user.id:
        node.edit_lock_at = datetime.utcnow()
        db.commit()
        db.refresh(node)
        return {"message": "编辑锁续期", "node": _serialize_task_node(db, node)}
    if force:
        node.edit_lock_user_id = current_user.id
        node.edit_lock_at = datetime.utcnow()
        db.commit()
        db.refresh(node)
        return {"message": "已抢锁", "node": _serialize_task_node(db, node)}
    hu = _username_by_id(db, node.edit_lock_user_id)
    raise HTTPException(
        status_code=409,
        detail=f"编辑锁由「{hu or node.edit_lock_user_id}」占用，需抢锁请传 force=true",
    )


@router.post("/nodes/{node_id}/release-edit-lock")
def release_edit_lock(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    node = require_task_node(db, current_user, node_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if node.edit_lock_user_id == current_user.id or workspace_data_full_control(db, current_user, node.workspace_id):
        node.edit_lock_user_id = None
        node.edit_lock_at = None
        db.commit()
    return {"message": "ok", "node": _serialize_task_node(db, node)}


@router.post("/nodes/{node_id}/publish")
def publish_node(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_RUN)
    assert_can_publish_production(db, current_user, node.workspace_id)
    node.is_published = True
    if settings.STUDIO_LOCK_ON_PUBLISH:
        node.is_locked = True
    if not node.owner_id:
        node.owner_id = current_user.id
    node.updated_at = datetime.utcnow()
    db.commit()
    msg = "发布成功，脚本已锁定" if settings.STUDIO_LOCK_ON_PUBLISH else "发布成功（未启用提交锁定，见 STUDIO_LOCK_ON_PUBLISH）"
    return {"message": msg, "node": _serialize_task_node(db, node)}


class RunNodeBody(BaseModel):
    """POST /studio/nodes/{id}/run：大段 SQL/脚本请放 JSON body，勿用 query（易超长、被代理截断或写入访问日志）。"""
    script_content: Optional[str] = None
    bizdate: Optional[str] = None  # YYYY-MM-DD；补数据/调度回调传入，宏相对该日展开


@router.post("/nodes/{node_id}/run")
def run_node(
    node_id: int,
    body: RunNodeBody = Body(default_factory=RunNodeBody),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.business_date import normalize_business_date

    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_RUN)

    # 用传入的最新内容覆盖，不需要先保存（调度器可不传 body，沿用库内脚本）
    if body.script_content is not None:
        node.script_content = body.script_content
    bizdate = normalize_business_date(body.bizdate)

    instance = NodeInstance(node_id=node_id, status="running", started_at=datetime.utcnow())
    db.add(instance)
    db.commit()
    db.refresh(instance)

    log_lines, status, result_data = [], "success", None
    try:
        if node.node_type == "SQL":
            from app.services.studio_sql_run import run_sql_with_result

            log_lines, result_data = run_sql_with_result(
                node, db, bizdate, resolve_date_expr=_resolve_date_expr
            )
        elif node.node_type == "PYTHON":
            log_lines = _run_python(node, db, bizdate=bizdate)
        elif node.node_type == "SHELL":
            log_lines = _run_shell(node, db, bizdate=bizdate)
        elif node.node_type == "SYNC":
            from app.services.integration_node import run_sync_for_node_blocking
            log_lines, status, _meta = run_sync_for_node_blocking(
                db, node, trigger_type="studio", timeout_seconds=node.timeout_seconds or 3600
            )
        elif node.node_type == "DEPENDENT":
            from app.services.workflow_dependent import check_dependent_local
            ok, log_lines = check_dependent_local(db, node)
            if not ok:
                status = "failed"
        else:
            log_lines = [f"[INFO] 节点类型 {node.node_type} 执行完成"]
    except Exception as e:
        status = "failed"
        log_lines.append(f"[ERROR] {str(e)}")

    instance.status = status
    instance.log_content = "\n".join(log_lines)
    instance.finished_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user.id, "run", "node", node.id, node.name, node.workspace_id)

    try:
        from app.services.adhoc_run_store import save_adhoc_run

        err = None
        if status == "failed" and log_lines:
            err = next((ln for ln in reversed(log_lines) if "[ERROR]" in ln), log_lines[-1])
        save_adhoc_run(
            db,
            workspace_id=node.workspace_id,
            source="studio",
            triggered_by=current_user.id,
            status=status,
            sql_text=node.script_content,
            datasource_id=node.datasource_id,
            object_name=node.name,
            node_id=node.id,
            node_instance_id=instance.id,
            error_message=err,
            log_content=instance.log_content,
            result=result_data if isinstance(result_data, dict) else None,
            started_at=instance.started_at,
            finished_at=instance.finished_at,
        )
    except Exception:
        pass

    return {"instance_id": instance.id, "status": status, "log": instance.log_content, "result": result_data}


def _resolve_date_expr(expr: str, bizdate: str = None, tz_name: str = "Asia/Shanghai") -> str:
    from app.services.date_macros import resolve_date_expr

    return resolve_date_expr(expr, bizdate, tz_name)


def _run_sql(node: TaskNode, db: Session, bizdate: str = None) -> list:
    from app.services.studio_sql_run import run_sql_with_result

    logs, _ = run_sql_with_result(node, db, bizdate, resolve_date_expr=_resolve_date_expr)
    return logs


def _run_python(node: TaskNode, db: Session, bizdate: str = None) -> list:
    """执行 PYTHON 节点：注入 gido_job SDK 与数据源上下文。"""
    return run_python_node(node, db, bizdate=bizdate)


@router.post("/internal/nodes/{node_id}/run")
def internal_run_node(
    node_id: int,
    body: RunNodeBody = Body(default_factory=RunNodeBody),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """供 Dolphin SHELL 回调执行节点；Bearer 须为 INTERNAL_TOKEN。

    支持 PYTHON / SQL（降级 SHELL 回调）。body.bizdate 由 DS 展开 ``$[yyyy-MM-dd]`` 传入。
    """
    from app.services.business_date import normalize_business_date

    token = (authorization or "").replace("Bearer ", "").strip()
    if not settings.INTERNAL_TOKEN or token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="无效的内部令牌")
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    if node.node_type not in ("PYTHON", "SQL"):
        raise HTTPException(
            status_code=400,
            detail=f"节点类型为 {node.node_type}，内部回调仅支持 PYTHON / SQL",
        )

    bizdate = normalize_business_date(body.bizdate)
    if body.script_content is not None:
        node.script_content = body.script_content

    instance = NodeInstance(node_id=node_id, status="running", started_at=datetime.utcnow())
    db.add(instance)
    db.commit()
    db.refresh(instance)

    log_lines: List[str] = []
    status = "success"
    try:
        if node.node_type == "SQL":
            log_lines = _run_sql(node, db, bizdate=bizdate)
        else:
            log_lines = _run_python(node, db, bizdate=bizdate)
    except Exception as e:
        status = "failed"
        log_lines.append(f"[ERROR] {str(e)}")

    instance.status = status
    instance.log_content = "\n".join(log_lines)
    instance.finished_at = datetime.utcnow()
    db.commit()

    if status != "success":
        raise HTTPException(
            status_code=500,
            detail=instance.log_content or f"{node.node_type} 节点执行失败",
        )
    return {"instance_id": instance.id, "status": status, "log": instance.log_content}


def _run_shell(node: TaskNode, db: Session = None, bizdate: str = None) -> list:
    """执行 SHELL 节点；与 SQL/PYTHON 一致，跑前展开空间全局变量 ${key} / 时间宏。"""
    import subprocess
    import tempfile
    import os

    from app.services.workspace_variables import substitute_script_variables

    script = node.script_content or ""
    extra = None
    params = getattr(node, "params", None) or {}
    if isinstance(params, dict) and params:
        extra = {str(k): "" if v is None else str(v) for k, v in params.items() if k is not None}

    if db is not None and getattr(node, "workspace_id", None) is not None:
        try:
            script = substitute_script_variables(
                db,
                int(node.workspace_id),
                script,
                "batch",
                bizdate=bizdate,
                extra_vars=extra,
            )
        except Exception:
            pass

    logs = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        tmp_path = f.name
    try:
        result = subprocess.run(["bash", tmp_path], capture_output=True, text=True, timeout=300)
        logs.append(result.stdout or "")
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
    finally:
        os.unlink(tmp_path)
    return logs


@router.get("/nodes/{node_id}/instances")
def get_node_instances(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """仅开发态「单节点试跑」记录（无工作流实例 id），不包含调度/工作流提交产生的节点实例。"""
    require_task_node(db, current_user, node_id)
    instances = (
        db.query(NodeInstance)
        .filter(NodeInstance.node_id == node_id, NodeInstance.workflow_instance_id.is_(None))
        .order_by(NodeInstance.id.desc())
        .limit(20)
        .all()
    )
    return instances


@router.get("/nodes/{node_id}/history")
def get_node_history(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """节点脚本版本历史（最近20次保存记录）"""
    require_task_node(db, current_user, node_id)
    from app.models.workspace import NodeHistory
    records = db.query(NodeHistory).filter(NodeHistory.node_id == node_id).order_by(NodeHistory.id.desc()).limit(20).all()
    return [{"id": r.id, "script_content": r.script_content, "saved_at": r.saved_at, "saved_by": r.saved_by} for r in records]


@router.post("/nodes/{node_id}/history/{history_id}/rollback")
def rollback_node(node_id: int, history_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """回滚到指定版本"""
    from app.models.workspace import NodeHistory
    node = require_task_node(db, current_user, node_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if getattr(node, "is_locked", False):
        raise HTTPException(status_code=403, detail="脚本已锁定，请先解锁后再回滚")
    _persist_clear_expired_edit_lock(db, node)
    if getattr(node, "edit_lock_user_id", None) and node.edit_lock_user_id != current_user.id:
        hu = _username_by_id(db, node.edit_lock_user_id)
        raise HTTPException(
            status_code=403,
            detail=f"编辑锁由「{hu or node.edit_lock_user_id}」占用，请先获取或抢锁后再回滚",
        )
    record = db.query(NodeHistory).filter(NodeHistory.id == history_id, NodeHistory.node_id == node_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="版本不存在")
    node.script_content = record.script_content
    node.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "回滚成功"}


@router.get("/nodes/{node_id}/dependencies")
def get_dependencies(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_task_node(db, current_user, node_id)
    deps = db.query(NodeDependency).filter(NodeDependency.node_id == node_id).all()
    return [{"depends_on_id": d.depends_on_id} for d in deps]


@router.post("/nodes/{node_id}/dependencies")
def add_dependency(node_id: int, depends_on_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if node_id == depends_on_id:
        raise HTTPException(status_code=400, detail="不能依赖自身")
    node = require_task_node(db, current_user, node_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    dep_node = require_task_node(db, current_user, depends_on_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if node.workspace_id != dep_node.workspace_id:
        raise HTTPException(status_code=400, detail="依赖节点必须属于同一工作空间")
    existing = db.query(NodeDependency).filter(
        NodeDependency.node_id == node_id,
        NodeDependency.depends_on_id == depends_on_id
    ).first()
    if not existing:
        db.add(NodeDependency(node_id=node_id, depends_on_id=depends_on_id))
        db.commit()
    return {"message": "依赖添加成功"}

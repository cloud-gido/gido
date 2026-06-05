# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
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

# 协作编辑锁过期时间（秒），过期后他人可直接占用或抢锁
EDIT_LOCK_TTL_SECONDS = 30 * 60

router = APIRouter(prefix="/studio", tags=["数据开发"])


def _username_by_id(db: Session, user_id: Optional[int]) -> Optional[str]:
    if not user_id:
        return None
    u = db.query(User).filter(User.id == user_id).first()
    return u.username if u else None


def _edit_lock_expired(node: TaskNode) -> bool:
    if not getattr(node, "edit_lock_user_id", None) or not getattr(node, "edit_lock_at", None):
        return True
    at = node.edit_lock_at
    if at is None:
        return True
    return (datetime.utcnow() - at).total_seconds() > EDIT_LOCK_TTL_SECONDS


def _effective_edit_lock_for_api(db: Session, node: TaskNode):
    """返回当前有效的编辑锁（过期则视为无锁，仅展示用；持久清理在 acquire/update）。"""
    uid = getattr(node, "edit_lock_user_id", None)
    at = getattr(node, "edit_lock_at", None)
    if not uid or not at:
        return None, None, None
    if _edit_lock_expired(node):
        return None, None, None
    return uid, _username_by_id(db, uid), at.isoformat() if hasattr(at, "isoformat") else None


def _persist_clear_expired_edit_lock(db: Session, node: TaskNode) -> None:
    if getattr(node, "edit_lock_user_id", None) and _edit_lock_expired(node):
        node.edit_lock_user_id = None
        node.edit_lock_at = None


def _serialize_task_node(db: Session, node: TaskNode) -> dict:
    lock_uid, lock_uname, lock_at_s = _effective_edit_lock_for_api(db, node)
    owner_id = node.owner_id
    creator_id = node.created_by
    owner_uname = _username_by_id(db, owner_id)
    creator_uname = _username_by_id(db, creator_id)
    return {
        "id": node.id,
        "workspace_id": node.workspace_id,
        "name": node.name,
        "node_type": node.node_type,
        "script_content": node.script_content,
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


# ==================== 文件夹 ====================

class FolderCreate(BaseModel):
    workspace_id: int
    name: str
    parent_id: Optional[int] = None


@router.get("/folders")
def list_folders(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_STUDIO_READ)
    folders = db.query(NodeFolder).filter(NodeFolder.workspace_id == workspace_id).all()
    return [{"id": f.id, "name": f.name, "parent_id": f.parent_id} for f in folders]


@router.post("/folders")
def create_folder(folder_in: FolderCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, folder_in.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if folder_in.parent_id is not None:
        parent = db.query(NodeFolder).filter(NodeFolder.id == folder_in.parent_id).first()
        if not parent:
            raise HTTPException(status_code=404, detail="父文件夹不存在")
        if parent.workspace_id != folder_in.workspace_id:
            raise HTTPException(status_code=400, detail="父文件夹与工作空间不一致")
    folder = NodeFolder(**folder_in.model_dump())
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return folder


@router.put("/folders/{folder_id}")
def rename_folder(folder_id: int, name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    folder.name = name
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    folder = db.query(NodeFolder).filter(NodeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    assert_workspace_data_capability(db, current_user, folder.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
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
    q = db.query(func.max(TaskNode.sort_order)).filter(TaskNode.workspace_id == workspace_id)
    if folder_id is None:
        q = q.filter(TaskNode.folder_id.is_(None))
    else:
        q = q.filter(TaskNode.folder_id == folder_id)
    mx = q.scalar()
    return (int(mx) if mx is not None else 0) + 10


@router.get("/nodes")
def list_nodes(workspace_id: int, folder_id: Optional[int] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_STUDIO_READ)
    q = db.query(TaskNode).filter(TaskNode.workspace_id == workspace_id)
    if folder_id is not None:
        q = q.filter(TaskNode.folder_id == folder_id)
    nodes = q.order_by(TaskNode.sort_order.asc(), TaskNode.id.asc()).all()
    return [_serialize_task_node(db, n) for n in nodes]


@router.post("/nodes")
def create_node(node_in: NodeCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, node_in.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if node_in.datasource_id is not None:
        ds = require_datasource_row(db, current_user, node_in.datasource_id)
        if ds.workspace_id != node_in.workspace_id:
            raise HTTPException(status_code=400, detail="数据源不属于该工作空间")
    node = TaskNode(
        **node_in.model_dump(),
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
def update_node(node_id: int, node_in: NodeCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
    # 保存历史版本
    if node.script_content:
        db.add(NodeHistory(node_id=node_id, script_content=node.script_content, saved_by=current_user.id))
    patch = node_in.model_dump(exclude_unset=True)
    patch.pop("workspace_id", None)
    patch.pop("sort_order", None)
    if "datasource_id" in patch and patch.get("datasource_id") is not None:
        ds = require_datasource_row(db, current_user, patch["datasource_id"])
        if ds.workspace_id != node.workspace_id:
            raise HTTPException(status_code=400, detail="数据源不属于该节点所在工作空间")
    for k, v in patch.items():
        setattr(node, k, v)
    node.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(node)
    return _serialize_task_node(db, node)


@router.delete("/nodes/{node_id}")
def delete_node(node_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_WRITE)
    if getattr(node, "is_locked", False) and not workspace_data_full_control(db, current_user, node.workspace_id):
        raise HTTPException(status_code=403, detail="节点已锁定，仅空间管理员或平台管理员可删除")
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


@router.post("/nodes/{node_id}/run")
def run_node(node_id: int, script_content: Optional[str] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    assert_workspace_data_capability(db, current_user, node.workspace_id, "developer", PC.GIDO_BATCH_STUDIO_RUN)

    # 用传入的最新内容覆盖，不需要先保存
    if script_content is not None:
        node.script_content = script_content

    instance = NodeInstance(node_id=node_id, status="running", started_at=datetime.utcnow())
    db.add(instance)
    db.commit()
    db.refresh(instance)

    log_lines, status, result_data = [], "success", None
    try:
        if node.node_type == "SQL":
            from app.services.studio_sql_run import run_sql_with_result

            log_lines, result_data = run_sql_with_result(node, db, resolve_date_expr=_resolve_date_expr)
        elif node.node_type == "PYTHON":
            log_lines = _run_python(node)
        elif node.node_type == "SHELL":
            log_lines = _run_shell(node)
        elif node.node_type == "SYNC":
            from app.services.integration_node import run_sync_for_node_blocking
            log_lines, status, _meta = run_sync_for_node_blocking(
                db, node, trigger_type="studio", timeout_seconds=node.timeout_seconds or 3600
            )
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
    return {"instance_id": instance.id, "status": status, "log": instance.log_content, "result": result_data}


def _resolve_date_expr(expr: str, bizdate: str = None, tz_name: str = "Asia/Shanghai") -> str:
    """
    解析动态日期表达式，对齐 GIDO/DolphinScheduler 规范
    支持格式:
      $[yyyy-MM-dd]        业务日期
      $[yyyy-MM-dd-1]      业务日期前1天
      $[yyyy-MM-dd+7]      业务日期后7天
      $[yyyyMMdd-1]        无分隔符格式
      $[yyyy-MM-dd HH:mm:ss]  包含时间
      $[HH:mm:ss]          当前时间
    如果不是 $[...] 格式，直接返回原字符串
    """
    import re, datetime as dt
    try:
        import pytz
        tz = pytz.timezone(tz_name)
        now = dt.datetime.now(tz)
    except Exception:
        now = dt.datetime.now()

    m = re.fullmatch(r'\$\[(.+)\]', expr.strip())
    if not m:
        return expr

    inner = m.group(1).strip()

    offset_days = 0
    offset_match = re.search(r'([+-]\d+)$', inner)
    if offset_match:
        offset_days = int(offset_match.group(1))
        inner = inner[:offset_match.start()]

    if bizdate:
        try:
            base_date = dt.datetime.strptime(bizdate, "%Y-%m-%d")
        except ValueError:
            base_date = now.replace(tzinfo=None)
    else:
        base_date = now.replace(tzinfo=None)

    target = base_date + dt.timedelta(days=offset_days)

    has_time = any(c in inner for c in ('H', 'm', 's'))
    if has_time:
        target = target.replace(hour=now.hour, minute=now.minute, second=now.second)

    fmt = inner
    fmt = fmt.replace('yyyy', '%Y').replace('MM', '%m').replace('dd', '%d')
    fmt = fmt.replace('HH', '%H').replace('mm', '%M').replace('ss', '%S')

    try:
        return target.strftime(fmt)
    except Exception:
        return expr


def _run_sql(node: TaskNode, db: Session, bizdate: str = None) -> list:
    from app.services.studio_sql_run import run_sql_with_result

    logs, _ = run_sql_with_result(node, db, bizdate, resolve_date_expr=_resolve_date_expr)
    return logs


def _run_python(node: TaskNode) -> list:
    import subprocess, tempfile, os
    logs = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(node.script_content or "")
        tmp_path = f.name
    try:
        result = subprocess.run(["python3", tmp_path], capture_output=True, text=True, timeout=300)
        logs.append(result.stdout or "")
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
    finally:
        os.unlink(tmp_path)
    return logs


def _run_shell(node: TaskNode) -> list:
    import subprocess, tempfile, os
    logs = []
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(node.script_content or "")
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

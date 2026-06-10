# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any, Dict, Tuple
from datetime import datetime
import logging
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import Workflow, WorkflowInstance, NodeInstance, TaskNode, User
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
from app.services.rbac import assert_workspace_data_capability, require_workflow, workspace_data_full_control
from app.services.publish_approval import assert_can_publish_production
from app.services.workflow_dag_validate import assert_cron_when_scheduled, mark_ds_needs_republish

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workflows", tags=["工作流"])


class WorkflowCreate(BaseModel):
    workspace_id: int
    name: str
    description: Optional[str] = None
    dag_config: Optional[Dict[str, Any]] = None  # {"nodes": [...], "edges": [...]}
    schedule_type: str = "manual"
    cron_expression: Optional[str] = None


class WorkflowOut(BaseModel):
    id: int
    workspace_id: int
    name: str
    description: Optional[str]
    dag_config: Optional[Dict[str, Any]]
    schedule_type: str
    cron_expression: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    created_by: Optional[int] = None
    created_by_username: Optional[str] = None
    updated_by: Optional[int] = None
    updated_by_username: Optional[str] = None
    dolphin_workflow_url: Optional[str] = None
    """已发布到 Dolphin 后，若本地定义/调度与引擎可能不一致，为 True（需再次「发布DS」对齐）。"""
    needs_ds_republish: Optional[bool] = None

    class Config:
        from_attributes = True


def _wf_user_brief(db: Session, user_id: Optional[int]) -> Tuple[Optional[int], Optional[str]]:
    if not user_id:
        return None, None
    u = db.query(User).filter(User.id == user_id).first()
    return user_id, (u.username if u else None)


def workflow_to_out(wf: Workflow, db: Session) -> WorkflowOut:
    from app.services.dolphin import dolphin_workflow_console_url
    dag = wf.dag_config or {}
    url = None
    if get_dolphin_runtime(db, wf.workspace_id).enabled and dag.get("ds_project_code") and dag.get("ds_process_code"):
        url = dolphin_workflow_console_url(
            dag["ds_project_code"], f"dw_{wf.id}_{wf.name}", db=db, workspace_id=wf.workspace_id
        )
    meta = dag.get("ds_meta") or {}
    needs = bool(meta.get("needs_republish"))
    cb, cbn = _wf_user_brief(db, wf.created_by)
    ub, ubn = _wf_user_brief(db, getattr(wf, "updated_by", None))
    return WorkflowOut(
        id=wf.id,
        workspace_id=wf.workspace_id,
        name=wf.name,
        description=wf.description,
        dag_config=wf.dag_config,
        schedule_type=wf.schedule_type,
        cron_expression=wf.cron_expression,
        is_active=wf.is_active,
        created_at=wf.created_at,
        updated_at=getattr(wf, "updated_at", None),
        created_by=cb,
        created_by_username=cbn,
        updated_by=ub,
        updated_by_username=ubn,
        dolphin_workflow_url=url,
        needs_ds_republish=needs if dag.get("ds_process_code") is not None else None,
    )


@router.get("", response_model=List[WorkflowOut])
def list_workflows(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_WORKFLOW_READ)
    rows = db.query(Workflow).filter(Workflow.workspace_id == workspace_id).all()
    return [workflow_to_out(w, db) for w in rows]


@router.post("", response_model=WorkflowOut)
def create_workflow(wf_in: WorkflowCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, wf_in.workspace_id, "developer", PC.GIDO_BATCH_WORKFLOW_WRITE)
    try:
        assert_cron_when_scheduled(wf_in.schedule_type, wf_in.cron_expression)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    wf = Workflow(**wf_in.model_dump(), created_by=current_user.id, updated_by=current_user.id)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return workflow_to_out(wf, db)


@router.post("/{wf_id}/publish-to-ds")
def publish_to_ds(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """将工作流发布到 DolphinScheduler（定义上线 + 可选定时）；与脚本 bulk 发布共用同一套校验与合并逻辑。"""
    from app.services.workflow_ds_publish import publish_workflow_to_ds

    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    assert_can_publish_production(db, current_user, wf.workspace_id)
    if not get_dolphin_runtime(db, wf.workspace_id).enabled:
        raise HTTPException(
            status_code=400,
            detail="DolphinScheduler 未启用：请在本工作空间「空间设置」配置，或设置环境变量 DS_ENABLED=true",
        )
    refresh_ds_client(db, wf.workspace_id)
    try:
        out = publish_workflow_to_ds(db, wf)
    except RuntimeError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"同步 DS 失败: {e}")
    wf2 = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if wf2:
        wf2.updated_by = current_user.id
        wf2.updated_at = datetime.utcnow()
        db.commit()
    return {"message": "已同步到 DolphinScheduler", **out}


@router.get("/{wf_id}", response_model=WorkflowOut)
def get_workflow(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wf = require_workflow(db, current_user, wf_id)
    return workflow_to_out(wf, db)


@router.put("/{wf_id}", response_model=WorkflowOut)
def update_workflow(wf_id: int, wf_in: WorkflowCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_WRITE)
    patch = wf_in.model_dump(exclude_unset=True)
    patch.pop("workspace_id", None)
    dirty_ds = {"dag_config", "name", "schedule_type", "cron_expression"}
    for k, v in patch.items():
        setattr(wf, k, v)
    wf.updated_at = datetime.utcnow()
    wf.updated_by = current_user.id
    try:
        assert_cron_when_scheduled(wf.schedule_type, wf.cron_expression)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dag = wf.dag_config or {}
    if dag.get("ds_process_code") is not None and bool(dirty_ds & patch.keys()):
        mark_ds_needs_republish(wf)
    db.commit()
    db.refresh(wf)
    # 已发布到 Dolphin 后，仅点「保存」不会走 publish-to-ds；Cron 若不推送则 DS 侧仍用旧表达式或从未上线定时
    if get_dolphin_runtime(db, wf.workspace_id).enabled and wf.schedule_type == "cron" and (wf.cron_expression or "").strip():
        dag = wf.dag_config or {}
        pr, pc = dag.get("ds_project_code"), dag.get("ds_process_code")
        if pr is not None and pc is not None:
            try:
                from app.services.dolphin import ds_client

                refresh_ds_client(db, wf.workspace_id)
                ds_client.set_schedule(int(pr), int(pc), wf.cron_expression.strip())
            except Exception as e:
                logger.warning("保存后同步 DS Cron 失败（可再点「发布DS」重试）: %s", e)
    return workflow_to_out(wf, db)


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_WRITE)
    dag = wf.dag_config or {}
    ds_process_code = dag.get("ds_process_code")
    ds_project_code = dag.get("ds_project_code")
    dolphin_deleted = False
    dolphin_note: Optional[str] = None
    if get_dolphin_runtime(db, wf.workspace_id).enabled and ds_process_code and ds_project_code:
        from app.services.dolphin import ds_client

        refresh_ds_client(db, wf.workspace_id)
        try:
            ds_client.delete_process_definition(int(ds_project_code), int(ds_process_code))
            dolphin_deleted = True
        except Exception as e:
            logger.warning(
                "删除工作流时同步删除 Dolphin 失败 wf_id=%s processCode=%s: %s",
                wf_id,
                ds_process_code,
                e,
            )
            dolphin_note = str(e)[:500]
    db.delete(wf)
    db.commit()
    msg = "删除成功"
    if ds_process_code and dolphin_deleted:
        msg = "已删除工作流，并已从 Dolphin 移除对应流程定义"
    elif ds_process_code and dolphin_note:
        msg = f"工作流已删除；Dolphin 流程未删除（{dolphin_note}），请在 DS 控制台手动清理"
    return {"message": msg, "dolphin_deleted": dolphin_deleted, "dolphin_note": dolphin_note}


@router.post("/{wf_id}/run")
def run_workflow(wf_id: int, business_date: Optional[str] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """手动触发：DS 开启时仅走 DolphinScheduler；否则走本地执行（开发环境）。"""
    from app.services.alert import alert_workflow_failed
    from app.services.lineage import auto_parse_lineage
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)

    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        dag = wf.dag_config or {}
        process_code = dag.get("ds_process_code")
        project_code = dag.get("ds_project_code")
        if not process_code or not project_code:
            raise HTTPException(status_code=400, detail="工作流尚未发布到 DolphinScheduler，请先发布上线")
        from app.services.dolphin import ds_client
        refresh_ds_client(db, wf.workspace_id)
        instance = WorkflowInstance(
            workflow_id=wf_id, status="running", trigger_type="manual",
            business_date=business_date or datetime.utcnow().strftime("%Y-%m-%d"),
            started_at=datetime.utcnow(),
            submitted_by=current_user.id,
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)
        try:
            ds_instance_id = ds_client.run_process(project_code, process_code, business_date)
            instance.trigger_type = f"manual|ds:{ds_instance_id}"
            db.commit()
            return {
                "instance_id": instance.id, "status": "running",
                "ds_instance_id": ds_instance_id, "message": "已提交到 DolphinScheduler",
            }
        except Exception as e:
            instance.status = "failed"
            db.commit()
            raise HTTPException(status_code=500, detail=f"Dolphin 触发失败: {e}")

    instance = WorkflowInstance(
        workflow_id=wf_id,
        status="running",
        trigger_type="manual",
        business_date=business_date or datetime.utcnow().strftime("%Y-%m-%d"),
        started_at=datetime.utcnow(),
        submitted_by=current_user.id,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)

    dag = wf.dag_config or {}
    ordered_nodes = _topo_sort(dag)
    errors = []

    for node_id in ordered_nodes:
        node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
        if not node:
            continue
        ni = NodeInstance(
            workflow_instance_id=instance.id,
            node_id=node_id,
            status="running",
            started_at=datetime.utcnow()
        )
        db.add(ni)
        db.commit()
        db.refresh(ni)
        try:
            from app.api.studio import _run_sql, _run_python, _run_shell
            if node.node_type == "SQL":
                logs = _run_sql(node, db)
                auto_parse_lineage(node, db)
            elif node.node_type == "PYTHON":
                logs = _run_python(node)
            elif node.node_type == "SHELL":
                logs = _run_shell(node)
            elif node.node_type == "SYNC":
                from app.services.integration_node import run_sync_for_node_blocking
                logs, st, _ = run_sync_for_node_blocking(
                    db, node, trigger_type="workflow", timeout_seconds=node.timeout_seconds or 3600
                )
                if st != "success":
                    raise RuntimeError("\n".join(logs))
            else:
                logs = [f"[INFO] {node.name} 执行完成"]
            ni.status = "success"
            ni.log_content = "\n".join(logs)
        except Exception as e:
            ni.status = "failed"
            ni.log_content = str(e)
            errors.append(f"节点 {node.name} 失败: {e}")
        ni.finished_at = datetime.utcnow()
        db.commit()

    instance.status = "failed" if errors else "success"
    instance.finished_at = datetime.utcnow()
    db.commit()

    if errors:
        alert_workflow_failed(wf.name, instance.id, errors)

    return {"instance_id": instance.id, "status": instance.status, "errors": errors}


def _topo_sort(dag: dict) -> list:
    """拓扑排序，返回节点执行顺序"""
    nodes = [n.get("node_id") for n in dag.get("nodes", []) if n.get("node_id")]
    edges = dag.get("edges", [])
    in_degree = {n: 0 for n in nodes}
    graph = {n: [] for n in nodes}
    for e in edges:
        src, tgt = e.get("source"), e.get("target")
        if src in graph and tgt in in_degree:
            graph[src].append(tgt)
            in_degree[tgt] += 1
    queue = [n for n in nodes if in_degree[n] == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for nxt in graph.get(node, []):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)
    for n in nodes:
        if n not in result:
            result.append(n)
    return result


@router.get("/{wf_id}/instances")
def list_instances(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_workflow(db, current_user, wf_id)
    from app.services.dolphin_instance_sync import refresh_running_ds_instances_for_workflow

    refresh_running_ds_instances_for_workflow(db, wf_id, limit=45)
    instances = db.query(WorkflowInstance).filter(WorkflowInstance.workflow_id == wf_id).order_by(WorkflowInstance.id.desc()).limit(50).all()
    result = []
    for inst in instances:
        node_insts = db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all()
        sb_id = getattr(inst, "submitted_by", None)
        _, sb_name = _wf_user_brief(db, sb_id)
        result.append({
            "id": inst.id,
            "status": inst.status,
            "trigger_type": inst.trigger_type,
            "business_date": inst.business_date,
            "started_at": inst.started_at,
            "finished_at": inst.finished_at,
            "submitted_by": sb_id,
            "submitted_by_username": sb_name,
            "node_instances": [{"node_id": ni.node_id, "status": ni.status, "log": ni.log_content} for ni in node_insts]
        })
    return result


@router.post("/{wf_id}/instances/{inst_id}/rerun")
def rerun_instance(wf_id: int, inst_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """失败实例重跑：DS 开启时提交 Dolphin；否则进入 pending 由本地执行器处理。"""
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    inst = db.query(WorkflowInstance).filter(
        WorkflowInstance.id == inst_id,
        WorkflowInstance.workflow_id == wf_id,
    ).first()
    if not inst:
        raise HTTPException(status_code=404, detail="实例不存在")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        dag = wf.dag_config or {}
        project_code, process_code = dag.get("ds_project_code"), dag.get("ds_process_code")
        if not project_code or not process_code:
            raise HTTPException(status_code=400, detail="工作流尚未发布到 DolphinScheduler")
        from app.services.dolphin import ds_client
        refresh_ds_client(db, wf.workspace_id)
        try:
            ds_instance_id = ds_client.run_process(project_code, process_code, inst.business_date)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Dolphin 重跑失败: {e}")
        inst.status = "running"
        inst.trigger_type = f"rerun|ds:{ds_instance_id}"
        inst.started_at = datetime.utcnow()
        inst.finished_at = None
        inst.submitted_by = current_user.id
        db.commit()
        return {"message": "已向 DolphinScheduler 提交重跑", "instance_id": inst.id, "ds_instance_id": ds_instance_id}
    inst.status = "pending"
    inst.trigger_type = "rerun"
    inst.submitted_by = current_user.id
    db.commit()
    return {"message": "已提交重跑（本地执行器将消费）", "instance_id": inst_id}


@router.post("/{wf_id}/batch-run")
def batch_run_workflow(
    wf_id: int,
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """批量补数据：按日期范围批量创建实例"""
    from datetime import datetime, timedelta
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")
    if (end - start).days > 90:
        raise HTTPException(status_code=400, detail="批量补数据最多90天")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        dag = wf.dag_config or {}
        project_code, process_code = dag.get("ds_project_code"), dag.get("ds_process_code")
        if not project_code or not process_code:
            raise HTTPException(status_code=400, detail="工作流尚未发布到 DolphinScheduler")
        from app.services.dolphin import ds_client
        refresh_ds_client(db, wf.workspace_id)
        dates: List[str] = []
        current = start
        while current <= end:
            bd = current.strftime("%Y-%m-%d")
            try:
                ds_id = ds_client.run_process(project_code, process_code, bd)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Dolphin 补数据失败 ({bd}): {e}")
            db.add(WorkflowInstance(
                workflow_id=wf_id,
                status="running",
                trigger_type=f"batch|ds:{ds_id}",
                business_date=bd,
                started_at=datetime.utcnow(),
                submitted_by=current_user.id,
            ))
            dates.append(bd)
            current += timedelta(days=1)
        db.commit()
        return {"message": f"已向 DolphinScheduler 提交 {len(dates)} 次补数据运行", "dates": dates}
    instances: List[str] = []
    current = start
    while current <= end:
        inst = WorkflowInstance(
            workflow_id=wf_id,
            status="pending",
            trigger_type="batch",
            business_date=current.strftime("%Y-%m-%d"),
            submitted_by=current_user.id,
        )
        db.add(inst)
        instances.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    db.commit()
    return {"message": f"已创建 {len(instances)} 个实例（本地执行器将消费）", "dates": instances}

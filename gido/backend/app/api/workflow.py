# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import Optional, List, Any, Dict, Tuple
from datetime import datetime
import logging
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import (
    AlertEvent,
    BackfillRequest,
    JobVersion,
    PublishApproval,
    Workflow,
    WorkflowInstance,
    NodeInstance,
    TaskNode,
    User,
)
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
    status: Optional[str] = None
    active_version_id: Optional[int] = None
    active_version_no: Optional[int] = None
    scheduler_engine: Optional[str] = None
    scheduler_definition_id: Optional[str] = None
    scheduler_project_id: Optional[str] = None
    dolphin_workflow_url: Optional[str] = None
    """已发布到生产调度后，若本地定义/调度与引擎可能不一致，为 True（需再次发布对齐）。"""
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
    project_id = getattr(wf, "scheduler_project_id", None)
    definition_id = getattr(wf, "scheduler_definition_id", None)
    if get_dolphin_runtime(db, wf.workspace_id).enabled and project_id and definition_id:
        url = dolphin_workflow_console_url(
            int(project_id), f"dw_{wf.id}_{wf.name}", db=db, workspace_id=wf.workspace_id
        )
    meta = dag.get("ds_meta") or {}
    needs = bool(meta.get("needs_republish"))
    active_version = _active_job_version(db, wf)
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
        status=getattr(wf, "status", None),
        active_version_id=getattr(wf, "active_version_id", None),
        active_version_no=getattr(active_version, "version_no", None),
        scheduler_engine=getattr(wf, "scheduler_engine", None) or "dolphin",
        scheduler_definition_id=str(definition_id) if definition_id is not None else None,
        scheduler_project_id=str(project_id) if project_id is not None else None,
        dolphin_workflow_url=url,
        needs_ds_republish=needs if definition_id is not None else None,
    )


def _active_job_version(db: Session, wf: Workflow) -> Optional[JobVersion]:
    if getattr(wf, "active_version_id", None):
        v = db.query(JobVersion).filter(JobVersion.id == wf.active_version_id, JobVersion.workflow_id == wf.id).first()
        if v:
            return v
    return (
        db.query(JobVersion)
        .filter(JobVersion.workflow_id == wf.id, JobVersion.status == "active")
        .order_by(JobVersion.version_no.desc(), JobVersion.id.desc())
        .first()
    )


def _purge_workflow_local_records(db: Session, wf: Workflow) -> None:
    """删除工作流前清理本地依赖行，避免外键约束导致 500。

    顺序：告警 → 节点实例 → 流程实例 → 补数 → 版本 → 审批单。
    TaskNode 脚本本身不随工作流删除（可被其他 DAG 复用）。
    """
    wf_id = int(wf.id)
    inst_ids = [
        row[0]
        for row in db.query(WorkflowInstance.id).filter(WorkflowInstance.workflow_id == wf_id).all()
    ]
    node_inst_ids: List[int] = []
    if inst_ids:
        node_inst_ids = [
            row[0]
            for row in db.query(NodeInstance.id).filter(NodeInstance.workflow_instance_id.in_(inst_ids)).all()
        ]

    alert_filters = [AlertEvent.workflow_id == wf_id]
    if inst_ids:
        alert_filters.append(AlertEvent.workflow_instance_id.in_(inst_ids))
    if node_inst_ids:
        alert_filters.append(AlertEvent.node_instance_id.in_(node_inst_ids))
    db.query(AlertEvent).filter(or_(*alert_filters)).delete(synchronize_session=False)

    if node_inst_ids:
        db.query(NodeInstance).filter(NodeInstance.id.in_(node_inst_ids)).delete(synchronize_session=False)
    if inst_ids:
        db.query(WorkflowInstance).filter(WorkflowInstance.id.in_(inst_ids)).delete(synchronize_session=False)

    db.query(BackfillRequest).filter(BackfillRequest.workflow_id == wf_id).delete(synchronize_session=False)

    wf.active_version_id = None
    db.flush()
    db.query(JobVersion).filter(JobVersion.workflow_id == wf_id).delete(synchronize_session=False)

    db.query(PublishApproval).filter(
        PublishApproval.resource_type == "workflow",
        PublishApproval.resource_id == wf_id,
    ).delete(synchronize_session=False)


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
    """将工作流发布到生产调度引擎（定义上线 + 可选定时）；与脚本 bulk 发布共用同一套校验与合并逻辑。"""
    from app.services.workflow_ds_publish import publish_workflow_to_ds

    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    assert_can_publish_production(db, current_user, wf.workspace_id)
    if not get_dolphin_runtime(db, wf.workspace_id).enabled:
        raise HTTPException(
            status_code=400,
            detail="生产调度引擎未启用：请在本工作空间「空间设置」配置，或设置环境变量 DS_ENABLED=true",
        )
    refresh_ds_client(db, wf.workspace_id)
    try:
        out = publish_workflow_to_ds(db, wf, published_by=current_user.id)
    except RuntimeError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"发布到生产调度失败: {e}")
    wf2 = db.query(Workflow).filter(Workflow.id == wf_id).first()
    if wf2:
        wf2.updated_by = current_user.id
        wf2.updated_at = datetime.utcnow()
        db.commit()
    return {"message": "已发布到生产调度", **out}


def _require_scheduler_definition(wf: Workflow) -> Tuple[str, str]:
    project_id = getattr(wf, "scheduler_project_id", None)
    definition_id = getattr(wf, "scheduler_definition_id", None)
    if not project_id or not definition_id:
        raise HTTPException(status_code=400, detail="工作流尚未发布生产版本")
    return str(project_id), str(definition_id)


def _scheduler_engine_for_workflow(wf: Workflow):
    from app.services.scheduler_engine import get_scheduler_engine

    return get_scheduler_engine(getattr(wf, "scheduler_engine", None) or "dolphin")


@router.post("/{wf_id}/pause")
def pause_workflow_schedule(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """暂停周期调度：保留生产定义和历史，禁止自动周期触发；仍允许手动运行/补数。"""
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    assert_can_publish_production(db, current_user, wf.workspace_id)
    project_id, definition_id = _require_scheduler_definition(wf)
    if not get_dolphin_runtime(db, wf.workspace_id).enabled:
        raise HTTPException(status_code=400, detail="生产调度引擎未启用")
    refresh_ds_client(db, wf.workspace_id)
    try:
        count = _scheduler_engine_for_workflow(wf).pause_schedule(project_id, definition_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"暂停调度失败: {e}")
    wf.status = "paused"
    wf.is_active = False
    wf.updated_by = current_user.id
    wf.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "已暂停周期调度", "workflow_id": wf.id, "status": wf.status, "schedules_touched": count}


@router.post("/{wf_id}/resume")
def resume_workflow_schedule(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """恢复周期调度：要求已有生产定义；没有调度配置时按当前 cron 重新设置。"""
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    assert_can_publish_production(db, current_user, wf.workspace_id)
    if getattr(wf, "status", None) == "offline":
        raise HTTPException(status_code=400, detail="工作流已下线，请重新发布上线")
    project_id, definition_id = _require_scheduler_definition(wf)
    if not get_dolphin_runtime(db, wf.workspace_id).enabled:
        raise HTTPException(status_code=400, detail="生产调度引擎未启用")
    refresh_ds_client(db, wf.workspace_id)
    engine = _scheduler_engine_for_workflow(wf)
    try:
        engine.online_definition(project_id, definition_id)
        count = 0
        if wf.schedule_type == "cron" and (wf.cron_expression or "").strip():
            count = engine.resume_schedule(project_id, definition_id)
            if count == 0:
                engine.set_schedule(project_id, definition_id, wf.cron_expression.strip())
                count = 1
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"恢复调度失败: {e}")
    wf.status = "published"
    wf.is_active = True
    wf.updated_by = current_user.id
    wf.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "已恢复生产调度", "workflow_id": wf.id, "status": wf.status, "schedules_touched": count}


@router.post("/{wf_id}/offline")
def offline_workflow(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """任务下线：停止周期并下线生产定义，保留 GIDO 定义、版本和历史实例。"""
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    assert_can_publish_production(db, current_user, wf.workspace_id)
    project_id, definition_id = _require_scheduler_definition(wf)
    if not get_dolphin_runtime(db, wf.workspace_id).enabled:
        raise HTTPException(status_code=400, detail="生产调度引擎未启用")
    refresh_ds_client(db, wf.workspace_id)
    engine = _scheduler_engine_for_workflow(wf)
    try:
        count = engine.pause_schedule(project_id, definition_id)
        engine.offline_definition(project_id, definition_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"任务下线失败: {e}")
    wf.status = "offline"
    wf.is_active = False
    wf.updated_by = current_user.id
    wf.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "任务已下线，历史实例已保留", "workflow_id": wf.id, "status": wf.status, "schedules_touched": count}


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
    if getattr(wf, "scheduler_definition_id", None) is not None and bool(dirty_ds & patch.keys()):
        mark_ds_needs_republish(wf)
    db.commit()
    db.refresh(wf)
    # 已发布到 Dolphin 后，仅点「保存」不会走 publish-to-ds；Cron 若不推送则 DS 侧仍用旧表达式或从未上线定时
    if get_dolphin_runtime(db, wf.workspace_id).enabled and wf.schedule_type == "cron" and (wf.cron_expression or "").strip():
        pr, pc = getattr(wf, "scheduler_project_id", None), getattr(wf, "scheduler_definition_id", None)
        if pr is not None and pc is not None:
            try:
                from app.services.dolphin import ds_client

                refresh_ds_client(db, wf.workspace_id)
                ds_client.set_schedule(int(pr), int(pc), wf.cron_expression.strip())
            except Exception as e:
                logger.warning("保存后同步调度 Cron 失败（可再点「发布生产」重试）: %s", e)
    return workflow_to_out(wf, db)


@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_WRITE)
    status = getattr(wf, "status", None) or "draft"
    if getattr(wf, "scheduler_definition_id", None) and status not in ("offline", "draft", "paused"):
        raise HTTPException(status_code=400, detail="已上线工作流不能直接删除，请先执行「任务下线」")
    ds_process_code = getattr(wf, "scheduler_definition_id", None)
    ds_project_code = getattr(wf, "scheduler_project_id", None)
    dolphin_deleted = False
    dolphin_note: Optional[str] = None
    # 有生产定义就尽量同步删掉 DS 流程与调度（与「下线」不同：删除会清调度任务本身）
    if ds_process_code and ds_project_code:
        from app.services.dolphin import ds_client

        try:
            refresh_ds_client(db, wf.workspace_id)
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
    try:
        _purge_workflow_local_records(db, wf)
        db.delete(wf)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        logger.exception("删除工作流本地记录失败 wf_id=%s", wf_id)
        raise HTTPException(
            status_code=409,
            detail=f"删除失败：仍有关联数据未清理（{str(e.orig) if getattr(e, 'orig', None) else e}）",
        ) from e
    except Exception as e:
        db.rollback()
        logger.exception("删除工作流失败 wf_id=%s", wf_id)
        raise HTTPException(status_code=500, detail=f"删除失败: {e}") from e
    msg = "删除成功"
    if ds_process_code and dolphin_deleted:
        msg = "已删除工作流，并已从调度引擎移除对应流程定义与周期调度"
    elif ds_process_code and dolphin_note:
        msg = f"工作流已删除；调度引擎流程未删除（{dolphin_note}），请在 Dolphin 中手动清理"
    return {"message": msg, "dolphin_deleted": dolphin_deleted, "dolphin_note": dolphin_note}


@router.post("/{wf_id}/run")
def run_workflow(wf_id: int, business_date: Optional[str] = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """手动触发：生产调度开启时走调度引擎；否则走本地执行（开发环境）。"""
    from app.services.alert import alert_workflow_failed
    from app.services.lineage import auto_parse_lineage
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    if getattr(wf, "status", None) == "offline":
        raise HTTPException(status_code=400, detail="工作流已下线，请重新发布上线后再运行")

    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        version = _active_job_version(db, wf)
        if not version:
            raise HTTPException(status_code=400, detail="工作流尚未发布生产版本，请先发布")
        process_code = version.scheduler_definition_id
        project_code = version.scheduler_project_id
        if not process_code or not project_code:
            raise HTTPException(status_code=400, detail="工作流尚未发布到生产调度，请先发布上线")
        from app.services.scheduler_engine import get_scheduler_engine
        refresh_ds_client(db, wf.workspace_id)
        instance = WorkflowInstance(
            workflow_id=wf_id, status="running", trigger_type="manual",
            job_version_id=version.id,
            scheduler_engine=version.scheduler_engine or "dolphin",
            scheduler_project_id=str(project_code),
            scheduler_definition_id=str(process_code),
            scheduler_definition_version=version.version_no,
            business_date=business_date or datetime.utcnow().strftime("%Y-%m-%d"),
            started_at=datetime.utcnow(),
            submitted_by=current_user.id,
        )
        db.add(instance)
        db.commit()
        db.refresh(instance)
        try:
            engine = get_scheduler_engine(version.scheduler_engine or "dolphin")
            ref = engine.trigger(str(project_code), str(process_code), business_date=business_date)
            instance.scheduler_engine = ref.engine
            instance.scheduler_instance_id = ref.instance_id
            instance.scheduler_run_key = f"{ref.engine}:{project_code}:{process_code}:{ref.instance_id}"[:128]
            instance.trigger_type = "manual"
            db.commit()
            return {
                "instance_id": instance.id, "status": "running",
                "scheduler_instance_id": ref.instance_id,
                "ds_instance_id": ref.instance_id,
                "message": "已提交到生产调度",
            }
        except Exception as e:
            instance.status = "failed"
            db.commit()
            raise HTTPException(status_code=500, detail=f"生产调度触发失败: {e}")

    instance = WorkflowInstance(
        workflow_id=wf_id,
        job_version_id=getattr(_active_job_version(db, wf), "id", None),
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
            bizdate = getattr(instance, "business_date", None)
            if node.node_type == "SQL":
                logs = _run_sql(node, db, bizdate=bizdate)
                auto_parse_lineage(node, db)
            elif node.node_type == "PYTHON":
                logs = _run_python(node, db, bizdate=bizdate)
            elif node.node_type == "SHELL":
                logs = _run_shell(node, db, bizdate=bizdate)
            elif node.node_type == "SYNC":
                from app.services.integration_node import run_sync_for_node_blocking
                logs, st, _ = run_sync_for_node_blocking(
                    db, node, trigger_type="workflow", timeout_seconds=node.timeout_seconds or 3600
                )
                if st != "success":
                    raise RuntimeError("\n".join(logs))
            elif node.node_type == "DEPENDENT":
                from app.services.workflow_dependent import check_dependent_local
                ok, logs = check_dependent_local(
                    db, node, business_date=getattr(instance, "business_date", None)
                )
                if not ok:
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
    """失败实例重跑：生产调度开启时提交调度引擎；否则进入 pending 由本地执行器处理。"""
    wf = require_workflow(db, current_user, wf_id, "developer", PC.GIDO_BATCH_WORKFLOW_RUN)
    inst = db.query(WorkflowInstance).filter(
        WorkflowInstance.id == inst_id,
        WorkflowInstance.workflow_id == wf_id,
    ).first()
    if not inst:
        raise HTTPException(status_code=404, detail="实例不存在")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        version = _active_job_version(db, wf)
        if not version:
            raise HTTPException(status_code=400, detail="工作流尚未发布生产版本")
        project_code = version.scheduler_project_id
        process_code = version.scheduler_definition_id
        if not project_code or not process_code:
            raise HTTPException(status_code=400, detail="工作流尚未发布到生产调度")
        from app.services.scheduler_engine import get_scheduler_engine
        refresh_ds_client(db, wf.workspace_id)
        try:
            engine = get_scheduler_engine(version.scheduler_engine or "dolphin")
            ref = engine.trigger(
                str(project_code),
                str(process_code),
                business_date=inst.business_date,
                complement=(inst.trigger_type or "") == "backfill",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"生产调度重跑失败: {e}")
        inst.status = "running"
        inst.job_version_id = version.id
        inst.scheduler_engine = ref.engine
        inst.scheduler_project_id = str(project_code)
        inst.scheduler_definition_id = str(process_code)
        inst.scheduler_definition_version = version.version_no
        inst.scheduler_instance_id = ref.instance_id
        inst.scheduler_run_key = f"{ref.engine}:{project_code}:{process_code}:{ref.instance_id}"[:128]
        inst.trigger_type = "rerun"
        inst.started_at = datetime.utcnow()
        inst.finished_at = None
        inst.submitted_by = current_user.id
        db.commit()
        return {
            "message": "已向生产调度提交重跑",
            "instance_id": inst.id,
            "scheduler_instance_id": ref.instance_id,
            "ds_instance_id": ref.instance_id,
        }
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
    if getattr(wf, "status", None) == "offline":
        raise HTTPException(status_code=400, detail="工作流已下线，请重新发布上线后再补数据")
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")
    if (end - start).days > 90:
        raise HTTPException(status_code=400, detail="批量补数据最多90天")
    if get_dolphin_runtime(db, wf.workspace_id).enabled:
        version = _active_job_version(db, wf)
        if not version:
            raise HTTPException(status_code=400, detail="工作流尚未发布生产版本")
        project_code = version.scheduler_project_id
        process_code = version.scheduler_definition_id
        if not project_code or not process_code:
            raise HTTPException(status_code=400, detail="工作流尚未发布到生产调度")
        from app.services.scheduler_engine import get_scheduler_engine
        refresh_ds_client(db, wf.workspace_id)
        engine = get_scheduler_engine(version.scheduler_engine or "dolphin")
        backfill = BackfillRequest(
            workflow_id=wf_id,
            job_version_id=version.id,
            date_start=start.strftime("%Y-%m-%d"),
            date_end=end.strftime("%Y-%m-%d"),
            status="running",
            total_instances=(end - start).days + 1,
            running_instances=(end - start).days + 1,
            submit_mode="daily",
            created_by=current_user.id,
        )
        db.add(backfill)
        db.flush()
        dates: List[str] = []
        current = start
        while current <= end:
            bd = current.strftime("%Y-%m-%d")
            try:
                # 必须走 Dolphin COMPLEMENT_DATA，否则调度时间为空、宏全按墙钟「昨天」
                ref = engine.trigger(
                    str(project_code),
                    str(process_code),
                    business_date=bd,
                    complement=True,
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"生产调度补数据失败 ({bd}): {e}")
            db.add(WorkflowInstance(
                workflow_id=wf_id,
                job_version_id=version.id,
                backfill_request_id=backfill.id,
                status="running",
                trigger_type="backfill",
                scheduler_engine=ref.engine,
                scheduler_project_id=str(project_code),
                scheduler_definition_id=str(process_code),
                scheduler_definition_version=version.version_no,
                scheduler_instance_id=ref.instance_id,
                scheduler_run_key=f"{ref.engine}:{project_code}:{process_code}:{ref.instance_id}"[:128],
                business_date=bd,
                started_at=datetime.utcnow(),
                submitted_by=current_user.id,
            ))
            dates.append(bd)
            current += timedelta(days=1)
        db.commit()
        return {"message": f"已向生产调度提交 {len(dates)} 次补数据运行", "dates": dates}
    instances: List[str] = []
    version = _active_job_version(db, wf)
    backfill = BackfillRequest(
        workflow_id=wf_id,
        job_version_id=getattr(version, "id", None),
        date_start=start.strftime("%Y-%m-%d"),
        date_end=end.strftime("%Y-%m-%d"),
        status="running",
        total_instances=(end - start).days + 1,
        running_instances=(end - start).days + 1,
        submit_mode="daily",
        created_by=current_user.id,
    )
    db.add(backfill)
    db.flush()
    current = start
    while current <= end:
        inst = WorkflowInstance(
            workflow_id=wf_id,
            job_version_id=getattr(version, "id", None),
            backfill_request_id=backfill.id,
            status="pending",
            trigger_type="backfill",
            business_date=current.strftime("%Y-%m-%d"),
            submitted_by=current_user.id,
        )
        db.add(inst)
        instances.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    db.commit()
    return {"message": f"已创建 {len(instances)} 个实例（本地执行器将消费）", "dates": instances}

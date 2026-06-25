# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""GIDO 运维动作：通过调度引擎 API 拉日志 / 终止 / 重试（Dolphin 为当前实现）。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.workspace import JobVersion, NodeInstance, TaskNode, Workflow, WorkflowInstance
from app.services.dolphin_instance_sync import _scheduler_instance_id_from_inst, _workflow_scheduler_refs
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client


def _project_code_for_node(db: Session, ni: NodeInstance, wf: Workflow, wf_inst: WorkflowInstance) -> Optional[int]:
    for raw in (
        getattr(ni, "scheduler_project_id", None),
        getattr(wf_inst, "scheduler_project_id", None),
    ):
        if raw is not None and str(raw).strip():
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                pass
    pc, _ = _workflow_scheduler_refs(wf)
    if pc is not None:
        return pc
    dag = wf.dag_config or {}
    raw = dag.get("ds_project_code")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    if getattr(wf_inst, "job_version_id", None):
        ver = db.query(JobVersion).filter(JobVersion.id == wf_inst.job_version_id).first()
        if ver and ver.scheduler_project_id:
            try:
                return int(ver.scheduler_project_id)
            except (TypeError, ValueError):
                pass
    return None


def _project_code_for_instance(db: Session, wf_inst: WorkflowInstance, wf: Workflow) -> Optional[int]:
    raw = getattr(wf_inst, "scheduler_project_id", None)
    if raw is not None and str(raw).strip():
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            pass
    if getattr(wf_inst, "job_version_id", None):
        ver = db.query(JobVersion).filter(JobVersion.id == wf_inst.job_version_id).first()
        if ver and ver.scheduler_project_id:
            try:
                return int(ver.scheduler_project_id)
            except (TypeError, ValueError):
                pass
    pc, _ = _workflow_scheduler_refs(wf)
    return pc


def _resolve_scheduler_task_instance_id(
    db: Session,
    ni: NodeInstance,
    wf: Workflow,
    wf_inst: WorkflowInstance,
    ds_client,
    project_code: int,
) -> Optional[int]:
    raw = getattr(ni, "scheduler_task_instance_id", None)
    if raw is not None and str(raw).strip().lstrip("-").isdigit():
        return int(str(raw).strip())
    ds_proc = _scheduler_instance_id_from_inst(wf_inst)
    if ds_proc is None:
        return None
    node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first()
    if not node or not (node.name or "").strip():
        return None
    try:
        tasks = ds_client.list_task_instances_all(project_code, ds_proc)
    except Exception:
        return None
    name = node.name.strip()
    for t in tasks:
        if (t.get("name") or "").strip() == name:
            tid = t.get("id") or t.get("taskInstanceId")
            if tid is not None:
                return int(tid)
    return None


def fetch_node_log_payload(
    db: Session,
    ni: NodeInstance,
    *,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    local = (ni.log_content or "").strip()
    base_payload = {
        "source": "gido",
        "status": "local",
        "message": "此处为节点在 GIDO Batch 侧记录的运行输出。",
        "log": local,
        "scheduler_task_instance_id": getattr(ni, "scheduler_task_instance_id", None),
        "scheduler_task_code": getattr(ni, "scheduler_task_code", None),
    }
    if not ni.workflow_instance_id:
        return base_payload
    wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
    if not wf_inst:
        return base_payload
    wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if not wf:
        return base_payload
    ws_id = workspace_id if workspace_id is not None else int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        return base_payload
    refresh_ds_client(db, ws_id)
    from app.services.dolphin import ds_client
    from app.services.scheduler_engine import get_scheduler_engine

    project_code = _project_code_for_node(db, ni, wf, wf_inst)
    if project_code is None:
        return {**base_payload, "status": "mapping_missing", "message": "未找到调度项目映射，展示 GIDO 本地日志。"}
    task_id = _resolve_scheduler_task_instance_id(db, ni, wf, wf_inst, ds_client, project_code)
    if task_id is None:
        return {**base_payload, "status": "task_mapping_missing", "message": "未解析到调度任务实例，展示 GIDO 本地日志。"}
    engine = get_scheduler_engine(getattr(wf_inst, "scheduler_engine", None) or "dolphin")
    ref = engine.get_task_log(str(task_id))
    payload = {
        "source": "scheduler" if ref.log else "gido",
        "status": ref.status,
        "message": ref.message,
        "log": ref.log or local,
        "scheduler_task_instance_id": str(task_id),
        "scheduler_task_code": getattr(ni, "scheduler_task_code", None),
    }
    if ref.log and ref.log.strip() != local:
        ni.log_content = ref.log
        ni.scheduler_error = None
        db.commit()
    elif ref.status not in ("available", "log_empty"):
        ni.scheduler_error = ref.message[:2000]
        db.commit()
    return payload


def fetch_node_log_from_scheduler(
    db: Session,
    ni: NodeInstance,
    *,
    workspace_id: Optional[int] = None,
) -> Tuple[str, str]:
    """
    返回 (log_text, source_hint)。
    优先 Dolphin task log；否则回退 GIDO 本地 log_content。
    """
    payload = fetch_node_log_payload(db, ni, workspace_id=workspace_id)
    return payload.get("log") or "", payload.get("message") or ""


def kill_node_via_scheduler(db: Session, ni: NodeInstance, *, workspace_id: Optional[int] = None) -> None:
    wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
    if not wf_inst:
        raise RuntimeError("节点未关联工作流实例")
    wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if not wf:
        raise RuntimeError("工作流不存在")
    ws_id = workspace_id if workspace_id is not None else int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        raise RuntimeError("生产调度未启用")
    refresh_ds_client(db, ws_id)
    from app.services.scheduler_engine import get_scheduler_engine

    project_code = _project_code_for_node(db, ni, wf, wf_inst)
    if project_code is None:
        raise RuntimeError("未找到调度项目映射")
    proc_id = _scheduler_instance_id_from_inst(wf_inst)
    if proc_id is not None:
        engine = get_scheduler_engine(getattr(wf_inst, "scheduler_engine", None) or "dolphin")
        engine.stop_instance(str(project_code), str(proc_id))
        return
    from app.services.dolphin import ds_client
    task_id = _resolve_scheduler_task_instance_id(db, ni, wf, wf_inst, ds_client, project_code)
    if task_id is not None:
        ds_client.stop_task_instance(project_code, task_id)
        return
    raise RuntimeError("未找到可终止的调度实例")


def retry_node_via_scheduler(db: Session, ni: NodeInstance, *, workspace_id: Optional[int] = None) -> None:
    wf_inst = db.query(WorkflowInstance).filter(WorkflowInstance.id == ni.workflow_instance_id).first()
    if not wf_inst:
        raise RuntimeError("节点未关联工作流实例")
    wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if not wf:
        raise RuntimeError("工作流不存在")
    ws_id = workspace_id if workspace_id is not None else int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        raise RuntimeError("生产调度未启用")
    refresh_ds_client(db, ws_id)
    from app.services.dolphin import ds_client
    from app.services.scheduler_engine import get_scheduler_engine

    project_code = _project_code_for_node(db, ni, wf, wf_inst)
    if project_code is None:
        raise RuntimeError("未找到调度项目映射")
    proc_id = _scheduler_instance_id_from_inst(wf_inst)
    if proc_id is None:
        raise RuntimeError("未找到调度流程实例")
    task_code = getattr(ni, "scheduler_task_code", None)
    engine = get_scheduler_engine(getattr(wf_inst, "scheduler_engine", None) or "dolphin")
    if task_code is not None and str(task_code).strip().lstrip("-").isdigit():
        engine.retry_task(str(project_code), str(proc_id), str(task_code).strip())
        return
    node = db.query(TaskNode).filter(TaskNode.id == ni.node_id).first()
    if node:
        dag = wf.dag_config or {}
        for n in dag.get("nodes", []) or []:
            if int(n.get("node_id") or 0) == int(node.id):
                for key in ("ds_task_code", "task_code"):
                    if n.get(key) is not None and str(n.get(key)).strip().lstrip("-").isdigit():
                        engine.retry_task(str(project_code), str(proc_id), str(n.get(key)).strip())
                        return
    engine.retry_failed_nodes(str(project_code), str(proc_id))


def stop_workflow_instance_via_scheduler(db: Session, wf_inst: WorkflowInstance) -> None:
    wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if not wf:
        raise RuntimeError("工作流不存在")
    ws_id = int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        raise RuntimeError("生产调度未启用")
    project_code = _project_code_for_instance(db, wf_inst, wf)
    proc_id = _scheduler_instance_id_from_inst(wf_inst)
    if project_code is None or proc_id is None:
        raise RuntimeError("未找到可终止的调度实例")
    refresh_ds_client(db, ws_id)
    from app.services.scheduler_engine import get_scheduler_engine

    engine = get_scheduler_engine(getattr(wf_inst, "scheduler_engine", None) or "dolphin")
    engine.stop_instance(str(project_code), str(proc_id))


def retry_failed_nodes_via_scheduler(db: Session, wf_inst: WorkflowInstance) -> None:
    wf = db.query(Workflow).filter(Workflow.id == wf_inst.workflow_id).first()
    if not wf:
        raise RuntimeError("工作流不存在")
    ws_id = int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        raise RuntimeError("生产调度未启用")
    project_code = _project_code_for_instance(db, wf_inst, wf)
    proc_id = _scheduler_instance_id_from_inst(wf_inst)
    if project_code is None or proc_id is None:
        raise RuntimeError("未找到可重试的调度实例")
    refresh_ds_client(db, ws_id)
    from app.services.scheduler_engine import get_scheduler_engine

    engine = get_scheduler_engine(getattr(wf_inst, "scheduler_engine", None) or "dolphin")
    engine.retry_failed_nodes(str(project_code), str(proc_id))

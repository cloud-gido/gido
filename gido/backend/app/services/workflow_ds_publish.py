# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""Workflow publishing with recoverable version and artifact state."""
from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from sqlalchemy import func

from app.models.workspace import (
    JobVersion,
    TaskNode,
    Workflow,
)
from app.services.dolphin import dolphin_workflow_console_url
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
from app.services.scheduler_engine import get_scheduler_engine
from app.services.workspace_datasource_policy import resolve_datasource_id
from app.services.workflow_dag_validate import (
    clear_ds_needs_republish,
    merge_dag_graph_into_config,
    validate_workflow_publishable,
)


def enrich_dag_from_db(db: Session, wf: Workflow) -> Dict:
    dag = wf.dag_config or {}
    enriched_nodes: List[Dict] = []
    for n in dag.get("nodes", []):
        node = db.query(TaskNode).filter(TaskNode.id == n.get("node_id")).first()
        if node:
            # 与 Studio SQL 运行一致：未单独配置 datasource_id 时继承工作空间默认，避免发布成 SHELL
            eff_ds_id = node.datasource_id
            if node.node_type in ("SQL", "PYTHON"):
                eff_ds_id = resolve_datasource_id(
                    db,
                    workspace_id=int(node.workspace_id),
                    explicit_datasource_id=node.datasource_id,
                )
            enriched_nodes.append(
                {
                    "node_id": node.id,
                    "name": node.name,
                    "node_type": node.node_type,
                    "script_content": node.script_content,
                    "datasource_id": eff_ds_id,
                    "retry_times": node.retry_times,
                    "retry_interval_minutes": getattr(node, "retry_interval_minutes", None),
                    "timeout_seconds": node.timeout_seconds,
                    "params": node.params or {},
                }
            )
    return {"nodes": enriched_nodes, "edges": dag.get("edges", [])}


def publish_workflow_to_ds(db: Session, wf: Workflow, *, published_by: Optional[int] = None) -> dict:
    """
    合并节点定义后同步到 Dolphin，上线并按需配置 Cron。
    与 GIDO / Airflow 等一致：发布前做 DAG + Cron 校验；合并 dag_config 以保留 ds_meta 等扩展字段。
    """
    workflow_id = int(wf.id)
    ws_id = int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        raise RuntimeError("DolphinScheduler 未启用（请在本工作空间「空间设置」配置 Dolphin，或设置环境变量 DS_ENABLED）")

    validate_workflow_publishable(db, wf)
    refresh_ds_client(db, ws_id)
    # PostgreSQL/MySQL serialize version allocation; the durable building marker below
    # continues serialization while the row lock is released around external side effects.
    locked_wf = (
        db.query(Workflow)
        .filter(Workflow.id == workflow_id)
        .with_for_update()
        .one()
    )
    source_dag = deepcopy(locked_wf.dag_config or {})
    enriched = enrich_dag_from_db(db, locked_wf)
    frozen_dag = merge_dag_graph_into_config(locked_wf, enriched)
    if not frozen_dag.get("nodes"):
        raise RuntimeError("工作流 DAG 为空，无法同步")
    next_version = (
        db.query(func.max(JobVersion.version_no))
        .filter(JobVersion.workflow_id == workflow_id)
        .scalar()
        or 0
    ) + 1
    building = (
        db.query(JobVersion.id)
        .filter(
            JobVersion.workflow_id == workflow_id,
            JobVersion.status == "building",
        )
        .first()
    )
    if building:
        raise RuntimeError("该工作流已有正在构建的生产版本，请稍后重试")
    build_started_at = datetime.utcnow()
    version = JobVersion(
        workflow_id=workflow_id,
        version_no=next_version,
        dag_snapshot=frozen_dag,
        cron_snapshot=locked_wf.cron_expression,
        schedule_type_snapshot=locked_wf.schedule_type,
        scheduler_engine=getattr(locked_wf, "scheduler_engine", None) or "dolphin",
        status="building",
        published_by=published_by,
        build_started_at=build_started_at,
    )
    db.add(version)
    db.flush()
    version_id = int(version.id)
    version.dag_snapshot = deepcopy(frozen_dag)
    flag_modified(version, "dag_snapshot")
    # Make the build ledger visible before any DS side effect. The building row also
    # serializes later publishers after the workflow row lock is released by this commit.
    db.commit()

    try:
        locked_wf = (
            db.query(Workflow)
            .filter(Workflow.id == workflow_id)
            .with_for_update()
            .one()
        )
        version = (
            db.query(JobVersion)
            .filter(JobVersion.id == version_id, JobVersion.status == "building")
            .one()
        )
        if (locked_wf.dag_config or {}) != source_dag:
            raise RuntimeError("发布期间工作流 DAG 已被修改；本次构建已终止，请重新发布最新草稿")
        # The engine reads workflow.dag_config. This assignment remains uncommitted until
        # every external call and all native-task diagnostics have succeeded.
        locked_wf.dag_config = deepcopy(frozen_dag)
        engine = get_scheduler_engine(version.scheduler_engine)
        ref = engine.publish_definition(locked_wf, db=db)
        project_code = int(ref.project_id)
        process_code = int(ref.definition_id)
        task_sync = ref.task_sync
        sql_shell = [
            d
            for d in task_sync
            if (d.get("node_type") or "").upper() == "SQL"
            and (d.get("ds_task_type") or "").upper() != "SQL"
        ]
        if sql_shell:
            parts = [
                f"节点#{d.get('node_id')}: {d.get('reason') or '未知原因'}"
                for d in sql_shell
            ]
            raise RuntimeError(
                "SQL 节点未能同步为 Dolphin SQL 任务（已降级为 SHELL 的逻辑被阻断）。 "
                + "；".join(parts)
            )
        engine.online_definition(str(project_code), str(process_code))
        if locked_wf.schedule_type == "cron" and locked_wf.cron_expression:
            from app.models.workspace import Workspace
            from app.services.schedule_policy import schedule_opts_from_workflow

            ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
            opts = schedule_opts_from_workflow(
                locked_wf, workspace_timezone=getattr(ws, "timezone", None)
            )
            engine.set_schedule(
                str(project_code),
                str(process_code),
                locked_wf.cron_expression,
                failure_strategy=opts["failure_strategy"],
                process_priority=opts["process_priority"],
                worker_group=opts["worker_group"],
                timezone_id=opts["timezone_id"],
            )

        code_by_node = {
            int(row["node_id"]): row.get("ds_task_code")
            for row in task_sync
            if row.get("node_id") is not None
        }
        for node in frozen_dag.get("nodes") or []:
            node_id = node.get("node_id")
            if node_id is not None and code_by_node.get(int(node_id)) is not None:
                node["ds_task_code"] = code_by_node[int(node_id)]
        version.dag_snapshot = deepcopy(frozen_dag)
        flag_modified(version, "dag_snapshot")
        locked_wf.dag_config = deepcopy(frozen_dag)
        flag_modified(locked_wf, "dag_config")

        db.query(JobVersion).filter(
            JobVersion.workflow_id == workflow_id,
            JobVersion.status == "active",
            JobVersion.id != version.id,
        ).update({"status": "archived"}, synchronize_session=False)
        now = datetime.utcnow()
        version.status = "active"
        version.scheduler_engine = ref.engine
        version.scheduler_definition_id = ref.definition_id
        version.scheduler_project_id = ref.project_id
        version.scheduler_definition_version = getattr(ref, "definition_version", None)
        version.build_finished_at = now
        version.published_at = now
        locked_wf.status = "published"
        locked_wf.is_active = True
        locked_wf.active_version_id = version.id
        locked_wf.scheduler_engine = ref.engine
        locked_wf.scheduler_definition_id = ref.definition_id
        locked_wf.scheduler_project_id = ref.project_id
        clear_ds_needs_republish(locked_wf)
        db.commit()
    except Exception as exc:
        # DS calls cannot be rolled back. Persist enough state for operators to inspect/retry,
        # while deliberately leaving the prior active version and workflow pointer untouched.
        db.rollback()
        version = db.query(JobVersion).filter(JobVersion.id == version_id).one()
        version.dag_snapshot = deepcopy(frozen_dag)
        flag_modified(version, "dag_snapshot")
        version.status = "failed"
        version.build_error = str(exc)[:10000]
        version.build_finished_at = datetime.utcnow()
        db.commit()
        raise

    db.refresh(locked_wf)
    search_name = f"dw_{locked_wf.id}_{locked_wf.name}"
    return {
        "workflow_id": locked_wf.id,
        "job_version_id": version.id,
        "version_no": version.version_no,
        "python_execution_mode": "callback",
        "scheduler_engine": ref.engine,
        "scheduler_definition_id": ref.definition_id,
        "scheduler_definition_version": version.scheduler_definition_version,
        "scheduler_project_id": ref.project_id,
        "ds_process_code": process_code,
        "ds_project_code": project_code,
        "ds_task_sync": task_sync,
        "dolphin_workflow_url": dolphin_workflow_console_url(
            project_code, search_name, db=db, workspace_id=locked_wf.workspace_id
        ),
    }


def bulk_publish_all_to_ds(
    db: Session, workspace_id: Optional[int] = None
) -> List[Dict]:
    q = db.query(Workflow).order_by(Workflow.id.asc())
    if workspace_id is not None:
        q = q.filter(Workflow.workspace_id == workspace_id)
    ids = [r.id for r in q.all()]
    results: List[Dict] = []
    for wid in ids:
        wf = db.query(Workflow).filter(Workflow.id == wid).first()
        if not wf:
            continue
        if not (wf.dag_config or {}).get("nodes"):
            results.append(
                {
                    "workflow_id": wf.id,
                    "name": wf.name,
                    "skipped": True,
                    "reason": "无 DAG 节点",
                }
            )
            continue
        try:
            payload = publish_workflow_to_ds(db, wf)
            results.append({"workflow_id": wf.id, "name": wf.name, "skipped": False, **payload})
        except Exception as e:
            db.rollback()
            results.append(
                {"workflow_id": wid, "name": wf.name, "skipped": False, "error": str(e)}
            )
    return results

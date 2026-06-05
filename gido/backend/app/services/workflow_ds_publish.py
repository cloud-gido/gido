# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作流 → DolphinScheduler：发布 / 批量再发布（修正 sqlType 等需推到 DS）。"""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.workspace import TaskNode, Workflow
from app.services.dolphin import ds_client, dolphin_workflow_console_url
from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
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
            if node.node_type == "SQL":
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
                    "timeout_seconds": node.timeout_seconds,
                    "params": node.params or {},
                }
            )
    return {"nodes": enriched_nodes, "edges": dag.get("edges", [])}


def publish_workflow_to_ds(db: Session, wf: Workflow) -> dict:
    """
    合并节点定义后同步到 Dolphin，上线并按需配置 Cron。
    与 GIDO / Airflow 等一致：发布前做 DAG + Cron 校验；合并 dag_config 以保留 ds_meta 等扩展字段。
    """
    ws_id = int(wf.workspace_id)
    if not get_dolphin_runtime(db, ws_id).enabled:
        raise RuntimeError("DolphinScheduler 未启用（请在本工作空间「空间设置」配置 Dolphin，或设置环境变量 DS_ENABLED）")

    validate_workflow_publishable(db, wf)
    refresh_ds_client(db, ws_id)
    enriched = enrich_dag_from_db(db, wf)
    wf.dag_config = merge_dag_graph_into_config(wf, enriched)
    dag_for_codes = wf.dag_config.get("nodes", [])
    if not dag_for_codes:
        raise RuntimeError("工作流 DAG 为空，无法同步")

    project_code = ds_client.get_or_create_project()
    process_code, task_sync = ds_client.sync_workflow(wf, db=db)
    sql_shell = [
        d
        for d in task_sync
        if (d.get("node_type") or "").upper() == "SQL" and (d.get("ds_task_type") or "").upper() != "SQL"
    ]
    if sql_shell:
        parts = [
            f"节点#{d.get('node_id')}: {d.get('reason') or '未知原因'}"
            for d in sql_shell
        ]
        raise RuntimeError(
            "SQL 节点未能同步为 Dolphin SQL 任务（已降级为 SHELL 的逻辑被阻断）。"
            + " " + "；".join(parts)
            + "。请先在 Dolphin「数据源中心」确认能否手工创建 Doris/MySQL 连接，"
            "或在 GIDO 数据源保存时查看 dolphin_sync 是否 ok，然后重新发布。"
        )
    ds_client.online_process(project_code, process_code)
    if wf.schedule_type == "cron" and wf.cron_expression:
        ds_client.set_schedule(project_code, process_code, wf.cron_expression)

    wf.dag_config["ds_process_code"] = process_code
    wf.dag_config["ds_project_code"] = project_code
    clear_ds_needs_republish(wf)
    db.commit()
    db.refresh(wf)
    search_name = f"dw_{wf.id}_{wf.name}"
    return {
        "workflow_id": wf.id,
        "ds_process_code": process_code,
        "ds_project_code": project_code,
        "ds_task_sync": task_sync,
        "dolphin_workflow_url": dolphin_workflow_console_url(
            project_code, search_name, db=db, workspace_id=wf.workspace_id
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

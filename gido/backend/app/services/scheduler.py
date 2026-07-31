# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
调度器服务：基于 APScheduler，自动触发 cron 任务
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _run_workflow_job(workflow_id: int):
    from app.services.distributed_lock import try_distributed_lock
    from app.services.shared_state import claim_once

    fire_key = datetime.utcnow().strftime("%Y%m%d%H%M")
    claimed = claim_once(f"workflow-schedule:{int(workflow_id)}:{fire_key}", 120)
    if claimed is False:
        logger.info("工作流 %s 本周期已由其他 backend 副本触发", workflow_id)
        return
    with try_distributed_lock(f"workflow-schedule:{int(workflow_id)}") as acquired:
        if not acquired:
            logger.info("工作流 %s 已由其他 backend 副本触发，本副本跳过", workflow_id)
            return
        _run_workflow_job_unlocked(workflow_id)


def _run_workflow_job_unlocked(workflow_id: int):
    from app.core.database import SessionLocal
    from app.models.workspace import Workflow, WorkflowInstance, TaskNode, NodeInstance
    from app.api.studio import _run_sql, _run_python, _run_shell
    from app.services.lineage import auto_parse_lineage
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
        if not wf or not wf.is_active:
            return
        instance = WorkflowInstance(
            workflow_id=workflow_id, status="running",
            trigger_type="schedule",
            business_date=datetime.now().strftime("%Y-%m-%d"),
            started_at=datetime.utcnow()
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
                workflow_instance_id=instance.id, node_id=node_id,
                status="running", started_at=datetime.utcnow()
            )
            db.add(ni)
            db.commit()
            db.refresh(ni)
            try:
                if node.node_type == "SQL":
                    logs = _run_sql(node, db)
                    auto_parse_lineage(node, db)
                elif node.node_type == "PYTHON":
                    logs = _run_python(node, db)
                elif node.node_type == "SHELL":
                    logs = _run_shell(node)
                elif node.node_type == "SYNC":
                    from app.services.integration_node import run_sync_for_node_blocking
                    logs, st, _ = run_sync_for_node_blocking(
                        db, node, trigger_type="schedule", timeout_seconds=node.timeout_seconds or 3600
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
                    logs = [f"[INFO] {node.name} 完成"]
                ni.status = "success"
                ni.log_content = "\n".join(logs)
            except Exception as e:
                ni.status = "failed"
                ni.log_content = str(e)
                errors.append(f"节点 {node.name}: {e}")
            ni.finished_at = datetime.utcnow()
            db.commit()

        instance.status = "failed" if errors else "success"
        instance.finished_at = datetime.utcnow()
        db.commit()
        logger.info(f"工作流 {wf.name} 调度执行完成: {instance.status}")
    finally:
        db.close()


def _topo_sort(dag: dict) -> list:
    """拓扑排序，返回节点执行顺序"""
    nodes = [n.get("node_id") for n in dag.get("nodes", []) if n.get("node_id")]
    edges = dag.get("edges", [])  # [{source: id, target: id}]
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
    # 未排到的节点追加（有环时兜底）
    for n in nodes:
        if n not in result:
            result.append(n)
    return result


def _run_sync_task_job(task_id: int):
    from app.services.integration_sync import start_sync_async
    from app.services.shared_state import claim_once

    try:
        fire_key = datetime.utcnow().strftime("%Y%m%d%H%M")
        claimed = claim_once(f"integration-schedule:{int(task_id)}:{fire_key}", 120)
        if claimed is False:
            logger.info("数据集成任务 %s 本周期已由其他 backend 副本触发", task_id)
            return
        start_sync_async(task_id, trigger_type="schedule")
        logger.info("数据集成任务 %s 定时触发已提交", task_id)
    except RuntimeError as e:
        logger.warning("数据集成任务 %s 跳过定时执行: %s", task_id, e)
    except Exception as e:
        logger.exception("数据集成任务 %s 定时执行失败: %s", task_id, e)


def _poll_scheduler_instances_job():
    """生产调度实例轮询补偿：回调丢失时仍能把实例状态写回 GIDO。"""
    from app.services.distributed_lock import try_distributed_lock
    from app.services.shared_state import claim_once

    bucket = int(datetime.utcnow().timestamp() // 30)
    claimed = claim_once(f"scheduler-instance-poll:{bucket}", 60)
    if claimed is False:
        return
    with try_distributed_lock("scheduler-instance-poll") as acquired:
        if not acquired:
            return
        _poll_scheduler_instances_unlocked()


def _poll_scheduler_instances_unlocked():
    from app.core.database import SessionLocal
    from app.services.ds_runtime import get_dolphin_runtime, refresh_ds_client
    from app.services.dolphin import ds_client
    from app.services.dolphin_instance_sync import patch_instances_from_ds_detail, sync_from_dolphin_definitions

    db = SessionLocal()
    try:
        if not get_dolphin_runtime(db).enabled:
            return
        refresh_ds_client(db)
        sync_from_dolphin_definitions(db, ds_client)
        patch_instances_from_ds_detail(db, ds_client, limit=120)
    except Exception as e:
        logger.warning("生产调度实例轮询同步失败: %s", e, exc_info=True)
    finally:
        db.close()


def reload_scheduler_instance_polling():
    """注册实例中心的调度引擎状态轮询补偿任务。"""
    for job in list(scheduler.get_jobs()):
        if job.id == "scheduler_instance_poll":
            job.remove()
    scheduler.add_job(
        _poll_scheduler_instances_job,
        IntervalTrigger(seconds=30),
        id="scheduler_instance_poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("已注册生产调度实例轮询补偿任务：30s")


def reload_integration_schedules():
    """注册数据集成 Cron 任务（与工作流调度独立）。"""
    from app.core.database import SessionLocal
    from app.models.workspace import SyncTask

    db = SessionLocal()
    try:
        for job in list(scheduler.get_jobs()):
            if job.id.startswith("sync_"):
                job.remove()
        tasks = (
            db.query(SyncTask)
            .filter(
                SyncTask.is_active.is_(True),
                SyncTask.schedule_cron.isnot(None),
                SyncTask.schedule_cron != "",
            )
            .all()
        )
        for t in tasks:
            cron = (t.schedule_cron or "").strip()
            if not cron:
                continue
            try:
                scheduler.add_job(
                    _run_sync_task_job,
                    CronTrigger.from_crontab(cron),
                    id=f"sync_{t.id}",
                    args=[t.id],
                    replace_existing=True,
                )
                logger.info("已注册数据集成调度: %s [%s]", t.name, cron)
            except Exception as e:
                logger.warning("数据集成 %s 调度注册失败: %s", t.name, e)
    finally:
        db.close()
    reload_scheduler_instance_polling()


def reload_schedules():
    """重新加载工作流调度任务（节点不独立调度，由工作流统一管理）"""
    from app.core.database import SessionLocal
    from app.models.workspace import Workflow
    from app.services.ds_runtime import get_dolphin_runtime
    db = SessionLocal()
    try:
        for job in scheduler.get_jobs():
            if job.id.startswith("wf_"):
                job.remove()

        if get_dolphin_runtime(db).enabled:
            logger.info("DolphinScheduler 已启用：定时调度由 DS 负责，已跳过 APScheduler 工作流注册（避免重复跑）")
        else:
            workflows = db.query(Workflow).filter(
                Workflow.schedule_type == "cron",
                Workflow.cron_expression != None,
                Workflow.is_active == True
            ).all()
            for wf in workflows:
                try:
                    scheduler.add_job(
                        _run_workflow_job, CronTrigger.from_crontab(wf.cron_expression),
                        id=f"wf_{wf.id}", args=[wf.id], replace_existing=True
                    )
                    logger.info(f"已注册工作流调度: {wf.name} [{wf.cron_expression}]")
                except Exception as e:
                    logger.warning(f"工作流 {wf.name} 调度注册失败: {e}")
    finally:
        db.close()
    reload_integration_schedules()


def start():
    if not scheduler.running:
        scheduler.start()
        reload_schedules()
        logger.info("调度器已启动")


def stop():
    if scheduler.running:
        scheduler.shutdown()

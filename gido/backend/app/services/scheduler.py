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

# 告警出站箱投递间隔。首次推送与失败重投都走这里；实际失败退避仍由
# alert_notification._next_retry_at 决定。5s 足够「准实时」，又不会和采集抢资源。
_NOTIFY_RETRY_INTERVAL_SEC = 5
# 基线巡检间隔：承诺时间与最长运行时长都以分钟计，60s 足够
_SLA_CHECK_INTERVAL_SEC = 60


def _run_workflow_job(workflow_id: int):
    from app.services.distributed_lock import try_distributed_lock
    from app.services.shared_state import claim_or_proceed

    fire_key = datetime.utcnow().strftime("%Y%m%d%H%M")
    # Redis 挂了也要照常触发：跨副本去重靠下面那把 advisory 锁兜底，
    # 绝不能因为共享状态不可用就让所有定时工作流集体不跑。
    if not claim_or_proceed(f"workflow-schedule:{int(workflow_id)}:{fire_key}", 120):
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
    from app.services.aps_workflow_schedule import is_workflow_aps_eligible
    db = SessionLocal()
    try:
        wf = db.query(Workflow).filter(Workflow.id == workflow_id).first()
        if not wf or not wf.is_active:
            return
        ok, reason = is_workflow_aps_eligible(db, wf)
        if not ok:
            logger.info(
                "跳过 APS 工作流定时 wf_id=%s name=%s：%s（应由 Dolphin 或已手动关闭）",
                workflow_id,
                getattr(wf, "name", None),
                reason,
            )
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
                    logs = _run_shell(node, db, bizdate=getattr(instance, "business_date", None))
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
        # 告警是 GIDO 自己的能力，不能只在外部调度引擎路径上成立
        _raise_or_clear_instance_alert(db, instance, errors)
        logger.info(f"工作流 {wf.name} 调度执行完成: {instance.status}")
    finally:
        db.close()


def _raise_or_clear_instance_alert(db, instance, errors: list) -> None:
    """本地/APS 执行完成后的告警闭环，与引擎采集路径共用同一套告警表与推送策略。"""
    from app.services.alert_center import open_instance_alert, resolve_instance_alerts_on_recovery

    try:
        if errors:
            detail = "；".join(errors[:3])
            suffix = f" 等 {len(errors)} 个节点失败" if len(errors) > 3 else ""
            open_instance_alert(db, workflow_instance=instance, message=f"{detail}{suffix}")
        else:
            resolve_instance_alerts_on_recovery(db, instance)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("工作流实例告警处理失败 instance_id=%s", getattr(instance, "id", None), exc_info=True)


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
    from app.services.shared_state import claim_or_proceed

    try:
        fire_key = datetime.utcnow().strftime("%Y%m%d%H%M")
        if not claim_or_proceed(f"integration-schedule:{int(task_id)}:{fire_key}", 120):
            logger.info("数据集成任务 %s 本周期已由其他 backend 副本触发", task_id)
            return
        # Redis 只是跨副本去重的优化，挂了也照常往下走；
        # 真正的入队互斥在 enqueue_sync_record 内部，重复触发会拿不到锁而抛 RuntimeError。
        start_sync_async(task_id, trigger_type="schedule")
        logger.info("数据集成任务 %s 定时触发已提交", task_id)
    except RuntimeError as e:
        logger.warning("数据集成任务 %s 跳过定时执行: %s", task_id, e)
    except Exception as e:
        logger.exception("数据集成任务 %s 定时执行失败: %s", task_id, e)


def _poll_scheduler_instances_job():
    """生产运行采集：不依赖用户打开页面，也不依赖引擎回调。"""
    from app.services.run_collector import COLLECT_INTERVAL_SEC
    from app.services.shared_state import claim_or_proceed

    bucket = int(datetime.utcnow().timestamp() // COLLECT_INTERVAL_SEC)
    if not claim_or_proceed(f"scheduler-instance-poll:{bucket}", 60):
        return
    # 互斥在 collect_runs 内部（拿不到锁会返回 collected=False）。这里不要再套一层锁：
    # advisory 锁走 engine.raw_connection()，每层都占一个池连接。
    _poll_scheduler_instances_unlocked()


def _poll_scheduler_instances_unlocked():
    from app.core.database import SessionLocal
    from app.services.run_collector import collect_runs

    db = SessionLocal()
    try:
        stats = collect_runs(db)
        if stats.get("ingested") or stats.get("updated_from_ds"):
            logger.info("生产运行采集完成 %s", stats)
    except Exception as e:
        logger.warning("生产运行采集失败: %s", e, exc_info=True)
    finally:
        db.close()


def _retry_alert_notifications_job():
    """告警出站箱投递：首次 pending + 失败重投 + 静默到期。与采集路径完全解耦。"""
    from app.core.database import SessionLocal
    from app.services.distributed_lock import try_distributed_lock
    from app.services.shared_state import claim_or_proceed

    bucket = int(datetime.utcnow().timestamp() // _NOTIFY_RETRY_INTERVAL_SEC)
    if not claim_or_proceed(f"alert-notify-retry:{bucket}", 120):
        return
    with try_distributed_lock("alert-notify-retry") as acquired:
        if not acquired:
            return
        from app.services.alert_notification import dispatch_pending_notifications

        db = SessionLocal()
        try:
            stats = dispatch_pending_notifications(db)
            if stats.get("due"):
                logger.info("告警通知投递完成 %s", stats)
        except Exception as e:
            logger.warning("告警通知投递失败: %s", e, exc_info=True)
        finally:
            db.close()


def reload_alert_notification_retry():
    """注册告警通知出站箱投递任务。"""
    for job in list(scheduler.get_jobs()):
        if job.id == "alert_notification_retry":
            job.remove()
    scheduler.add_job(
        _retry_alert_notifications_job,
        IntervalTrigger(seconds=_NOTIFY_RETRY_INTERVAL_SEC),
        id="alert_notification_retry",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("已注册告警通知投递任务：%ss", _NOTIFY_RETRY_INTERVAL_SEC)


def _evaluate_sla_job():
    """基线巡检：未按时完成与运行超时。"""
    from app.core.database import SessionLocal
    from app.services.distributed_lock import try_distributed_lock
    from app.services.shared_state import claim_or_proceed

    bucket = int(datetime.utcnow().timestamp() // _SLA_CHECK_INTERVAL_SEC)
    if not claim_or_proceed(f"sla-check:{bucket}", 120):
        return
    with try_distributed_lock("sla-check") as acquired:
        if not acquired:
            return
        from app.services.sla_monitor import evaluate_sla

        db = SessionLocal()
        try:
            stats = evaluate_sla(db)
            if stats.get("missed_deadline") or stats.get("long_running"):
                logger.info("基线巡检完成 %s", stats)
        except Exception as e:
            logger.warning("基线巡检失败: %s", e, exc_info=True)
        finally:
            db.close()
    # 锁已释放：把本轮基线告警推进出站箱
    from app.services.alert_notification import kick_alert_dispatch

    kick_alert_dispatch()


def reload_sla_monitoring():
    """注册基线巡检任务。"""
    for job in list(scheduler.get_jobs()):
        if job.id == "sla_check":
            job.remove()
    scheduler.add_job(
        _evaluate_sla_job,
        IntervalTrigger(seconds=_SLA_CHECK_INTERVAL_SEC),
        id="sla_check",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("已注册基线巡检任务：%ss", _SLA_CHECK_INTERVAL_SEC)


def reload_scheduler_instance_polling():
    """注册生产运行采集任务：把执行引擎上的运行持续写回 GIDO 实例事实表。"""
    from app.services.run_collector import COLLECT_INTERVAL_SEC

    for job in list(scheduler.get_jobs()):
        if job.id == "scheduler_instance_poll":
            job.remove()
    scheduler.add_job(
        _poll_scheduler_instances_job,
        IntervalTrigger(seconds=COLLECT_INTERVAL_SEC),
        id="scheduler_instance_poll",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    logger.info("已注册生产运行采集任务：%ss", COLLECT_INTERVAL_SEC)


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
    reload_alert_notification_retry()
    reload_sla_monitoring()


def reload_schedules():
    """重新加载工作流调度任务（节点不独立调度，由工作流统一管理）"""
    from app.core.database import SessionLocal
    from app.models.workspace import Workflow
    from app.services.aps_workflow_schedule import (
        is_workflow_aps_eligible,
        resolve_aps_workflow_master_switch,
    )
    db = SessionLocal()
    try:
        for job in scheduler.get_jobs():
            if job.id.startswith("wf_"):
                job.remove()

        master_ok, master_reason = resolve_aps_workflow_master_switch(db)
        if not master_ok:
            logger.info("已跳过 APScheduler 工作流注册：%s", master_reason)
        else:
            workflows = db.query(Workflow).filter(
                Workflow.schedule_type == "cron",
                Workflow.cron_expression != None,
                Workflow.is_active == True
            ).all()
            registered = 0
            skipped = 0
            for wf in workflows:
                ok, reason = is_workflow_aps_eligible(db, wf)
                if not ok:
                    skipped += 1
                    logger.info("跳过 APS 注册 %s：%s", wf.name, reason)
                    continue
                try:
                    scheduler.add_job(
                        _run_workflow_job, CronTrigger.from_crontab(wf.cron_expression),
                        id=f"wf_{wf.id}", args=[wf.id], replace_existing=True
                    )
                    registered += 1
                    logger.info("已注册工作流调度: %s [%s]", wf.name, wf.cron_expression)
                except Exception as e:
                    logger.warning("工作流 %s 调度注册失败: %s", wf.name, e)
            logger.info(
                "APS 工作流注册完成：registered=%s skipped=%s（%s）",
                registered,
                skipped,
                master_reason,
            )
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

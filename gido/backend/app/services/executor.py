# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
实时执行器：后台线程轮询 pending 状态的工作流实例并执行
"""
import threading
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
_executor_thread = None
_running = False


def _executor_loop():
    """执行器主循环"""
    from app.core.database import SessionLocal
    from app.models.workspace import WorkflowInstance, Workflow, TaskNode, NodeInstance
    from app.api.studio import _run_sql, _run_python, _run_shell
    from app.services.lineage import auto_parse_lineage
    from app.services.alert import alert_workflow_failed

    logger.info("实时执行器已启动")
    while _running:
        db = SessionLocal()
        try:
            # 查找 pending 实例（限制并发数）
            pending = db.query(WorkflowInstance).filter(
                WorkflowInstance.status == "pending"
            ).order_by(WorkflowInstance.id).limit(5).all()

            for inst in pending:
                wf = db.query(Workflow).filter(Workflow.id == inst.workflow_id).first()
                if not wf:
                    inst.status = "failed"
                    db.commit()
                    continue

                logger.info(f"开始执行工作流实例 {inst.id} (工作流: {wf.name})")
                inst.status = "running"
                inst.started_at = datetime.utcnow()
                db.commit()

                dag = wf.dag_config or {}
                ordered_nodes = _topo_sort(dag)
                errors = []

                for node_id in ordered_nodes:
                    node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
                    if not node:
                        continue

                    ni = NodeInstance(
                        workflow_instance_id=inst.id,
                        node_id=node_id,
                        status="running",
                        started_at=datetime.utcnow()
                    )
                    db.add(ni)
                    db.commit()
                    db.refresh(ni)

                    try:
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
                        errors.append(f"节点 {node.name}: {e}")
                        logger.error(f"节点 {node.name} 执行失败: {e}")

                    ni.finished_at = datetime.utcnow()
                    db.commit()

                inst.status = "failed" if errors else "success"
                inst.finished_at = datetime.utcnow()
                db.commit()

                if errors:
                    alert_workflow_failed(wf.name, inst.id, errors)

                logger.info(f"工作流实例 {inst.id} 执行完成: {inst.status}")

        except Exception as e:
            logger.error(f"执行器异常: {e}")
        finally:
            db.close()

        time.sleep(5)  # 每 5 秒轮询一次

    logger.info("实时执行器已停止")


def _topo_sort(dag: dict) -> list:
    """拓扑排序"""
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


def start():
    """启动执行器"""
    global _executor_thread, _running
    if _running:
        return
    _running = True
    _executor_thread = threading.Thread(target=_executor_loop, daemon=True)
    _executor_thread.start()
    logger.info("实时执行器线程已启动")


def stop():
    """停止执行器"""
    global _running
    _running = False
    if _executor_thread:
        _executor_thread.join(timeout=10)
    logger.info("实时执行器已停止")

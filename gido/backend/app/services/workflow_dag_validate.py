# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作流 DAG / 调度校验 — 发布前与保存时复用，对齐编排平台常见约束。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.workspace import TaskNode, Workflow


def assert_cron_when_scheduled(schedule_type: str, cron_expression: Optional[str]) -> None:
    if (schedule_type or "").strip() != "cron":
        return
    raw = (cron_expression or "").strip()
    if not raw:
        raise ValueError("选择 Cron 定时时必须填写调度表达式")
    parts = raw.split()
    if len(parts) != 5:
        raise ValueError("Cron 须为 5 段（Linux 风格）：分 时 日 月 周，例如 0 2 * * *")


def validate_dag_structure(dag: Dict[str, Any]) -> None:
    """校验 DAG 拓扑与节点引用（不查库）。"""
    nodes = dag.get("nodes") or []
    edges = dag.get("edges") or []
    if not nodes:
        raise ValueError("DAG 至少包含一个节点后再发布")

    ids: List[int] = []
    seen: Set[int] = set()
    for n in nodes:
        nid = n.get("node_id")
        if nid is None:
            raise ValueError("DAG 中存在缺少 node_id 的节点")
        if not isinstance(nid, int):
            try:
                nid = int(nid)
            except (TypeError, ValueError):
                raise ValueError(f"非法 node_id: {nid!r}")
        if nid in seen:
            raise ValueError(f"DAG 中节点 {nid} 重复出现")
        seen.add(nid)
        ids.append(nid)

    id_set = set(ids)
    for i, e in enumerate(edges):
        s, t = e.get("source"), e.get("target")
        if s is None or t is None:
            raise ValueError(f"第 {i + 1} 条边缺少 source 或 target")
        try:
            s, t = int(s), int(t)
        except (TypeError, ValueError):
            raise ValueError(f"边的 source/target 须为整数节点 id，当前: {e!r}")
        if s not in id_set or t not in id_set:
            raise ValueError(f"边 {s} → {t} 引用了不在 DAG 中的节点")

    if _dag_has_cycle(ids, edges):
        raise ValueError("DAG 存在环路，请检查依赖边后再发布")


def _dag_has_cycle(node_ids: List[int], edges: List[Dict[str, Any]]) -> bool:
    graph: Dict[int, List[int]] = {n: [] for n in node_ids}
    for e in edges:
        s, t = int(e["source"]), int(e["target"])
        if s in graph and t in graph:
            graph[s].append(t)
    # 0=未访问 1=栈中 2=已完成
    color: Dict[int, int] = {n: 0 for n in node_ids}

    def dfs(u: int) -> bool:
        color[u] = 1
        for v in graph.get(u, []):
            if color.get(v, 0) == 1:
                return True
            if color.get(v, 0) == 0 and dfs(v):
                return True
        color[u] = 2
        return False

    for n in node_ids:
        if color[n] == 0 and dfs(n):
            return True
    return False


def validate_workflow_publishable(db: Session, wf: Workflow) -> None:
    """发布到调度引擎前：DAG 结构 + 节点归属。"""
    assert_cron_when_scheduled(wf.schedule_type, wf.cron_expression)
    dag = wf.dag_config or {}
    validate_dag_structure(dag)

    for n in dag.get("nodes") or []:
        nid = int(n["node_id"])
        node = db.query(TaskNode).filter(TaskNode.id == nid).first()
        if not node:
            raise ValueError(f"节点不存在: id={nid}")
        if node.workspace_id != wf.workspace_id:
            raise ValueError(f"节点 {nid} 不属于当前工作流所在工作空间")


def merge_dag_graph_into_config(wf: Workflow, graph: Dict[str, Any]) -> Dict[str, Any]:
    """将仅含 nodes/edges 的编排结果合并进 dag_config，保留 ds_*、ds_meta 等扩展字段。"""
    base: Dict[str, Any] = dict(wf.dag_config or {})
    base["nodes"] = graph.get("nodes") or []
    base["edges"] = graph.get("edges") or []
    return base


def mark_ds_needs_republish(wf: Workflow) -> None:
    """已对接 Dolphin 时，标记「定义与引擎可能不一致」，需再次发布对齐。"""
    dag = dict(wf.dag_config or {})
    if dag.get("ds_process_code") is None:
        return
    meta = dict(dag.get("ds_meta") or {})
    meta["needs_republish"] = True
    dag["ds_meta"] = meta
    wf.dag_config = dag


def clear_ds_needs_republish(wf: Workflow) -> None:
    dag = dict(wf.dag_config or {})
    meta = dict(dag.get("ds_meta") or {})
    meta["needs_republish"] = False
    dag["ds_meta"] = meta
    wf.dag_config = dag

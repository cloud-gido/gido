# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""工作流 QUALITY 节点：绑定表/规则，本地与 Dolphin 回调共用。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.workspace import QualityRule, TaskNode
from app.services.quality_check import run_rule_check, run_table_checks


def quality_target_from_node(node: TaskNode) -> Tuple[Optional[int], Optional[int]]:
    params = node.params if isinstance(node.params, dict) else {}
    table_id = params.get("table_id")
    rule_id = params.get("rule_id")
    try:
        table_id = int(table_id) if table_id is not None and str(table_id).strip() != "" else None
    except (TypeError, ValueError):
        table_id = None
    try:
        rule_id = int(rule_id) if rule_id is not None and str(rule_id).strip() != "" else None
    except (TypeError, ValueError):
        rule_id = None
    return table_id, rule_id


def normalize_quality_params(raw: Optional[dict]) -> Dict[str, Any]:
    params = dict(raw or {})
    table_id = params.get("table_id")
    rule_id = params.get("rule_id")
    try:
        table_id = int(table_id) if table_id is not None and str(table_id).strip() != "" else None
    except (TypeError, ValueError):
        table_id = None
    try:
        rule_id = int(rule_id) if rule_id is not None and str(rule_id).strip() != "" else None
    except (TypeError, ValueError):
        rule_id = None
    out: Dict[str, Any] = {"table_id": table_id, "rule_id": rule_id}
    return out


def run_quality_for_node_blocking(
    db: Session,
    node: TaskNode,
    *,
    bizdate: Optional[str] = None,
    trigger: str = "workflow",
    notify: bool = True,
) -> Tuple[List[str], str, Dict[str, Any]]:
    """执行 QUALITY 节点。强规则失败时 status=failed（供本地执行器与内部回调）。"""
    table_id, rule_id = quality_target_from_node(node)
    logs: List[str] = []
    if not table_id and not rule_id:
        return ["QUALITY 节点未绑定 table_id 或 rule_id"], "failed", {}

    if table_id:
        logs.append(f"[INFO] 表级质量检查 table_id={table_id} bizdate={bizdate or 'T-1'}")
        result = run_table_checks(
            db,
            table_id,
            bizdate=bizdate,
            notify=notify,
            trigger=trigger,
        )
        logs.append(
            f"[INFO] 共 {result.get('total', 0)} 条规则 · 通过 {result.get('pass', 0)} · 失败 {result.get('fail', 0)}"
        )
        for one in result.get("results") or []:
            logs.append(
                f"[INFO] · {one.get('rule_name')} => {one.get('status')} score={one.get('score')} "
                f"block={one.get('should_block')}"
            )
        if result.get("should_block"):
            logs.append("[ERROR] 强规则失败，应阻断下游（should_block=true）")
            return logs, "failed", result
        if result.get("fail"):
            logs.append("[WARN] 存在失败规则，但均为告警级，不阻断下游")
        return logs, "success", result

    rule = db.query(QualityRule).filter(QualityRule.id == rule_id).first()
    if not rule:
        return [f"质量规则 #{rule_id} 不存在"], "failed", {}
    logs.append(f"[INFO] 单规则检查 rule_id={rule_id} name={rule.rule_name}")
    one = run_rule_check(db, rule, bizdate=bizdate, notify=notify, trigger=trigger)
    logs.append(f"[INFO] => {one.get('status')} score={one.get('score')} block={one.get('should_block')}")
    if one.get("should_block"):
        logs.append("[ERROR] 强规则失败，应阻断下游（should_block=true）")
        return logs, "failed", one
    if one.get("status") == "fail":
        logs.append("[WARN] 规则失败但为告警级，不阻断下游")
    return logs, "success", one


def assert_quality_params_for_publish(node: TaskNode) -> None:
    table_id, rule_id = quality_target_from_node(node)
    if not table_id and not rule_id:
        raise HTTPException(
            status_code=400,
            detail=f"质量节点「{node.name}」未绑定表或规则，无法发布到调度",
        )

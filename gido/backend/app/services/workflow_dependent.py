# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""跨工作流 DEPENDENT：参数校验、Dolphin payload、本地实例检查。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.workspace import TaskNode, Workflow, WorkflowInstance

# Dolphin dateValue → cycle（与 DS UI 常用子集对齐；大小写敏感）
DATE_VALUE_CYCLE: Dict[str, str] = {
    "currentHour": "hour",
    "last1Hour": "hour",
    "last2Hours": "hour",
    "last3Hours": "hour",
    "last24Hours": "hour",
    "today": "day",
    "yesterday": "day",
    "last1Days": "day",
    "last2Days": "day",
    "last3Days": "day",
    "last7Days": "day",
    "thisWeek": "week",
    "lastWeek": "week",
    "thisMonth": "month",
    "lastMonth": "month",
}
ALLOWED_DATE_VALUES = frozenset(DATE_VALUE_CYCLE.keys())
ALLOWED_RELATIONS = frozenset({"AND", "OR"})
DEFAULT_CYCLE = "day"
DEFAULT_DATE_VALUE = "today"
DEFAULT_RELATION = "AND"

# 旧配置小写别名 → 规范 dateValue
_DATE_VALUE_ALIASES = {
    "today": "today",
    "yesterday": "yesterday",
    "currenthour": "currentHour",
    "last1hour": "last1Hour",
    "last2hours": "last2Hours",
    "last3hours": "last3Hours",
    "last24hours": "last24Hours",
    "last1days": "last1Days",
    "last2days": "last2Days",
    "last3days": "last3Days",
    "last7days": "last7Days",
    "thisweek": "thisWeek",
    "lastweek": "lastWeek",
    "thismonth": "thisMonth",
    "lastmonth": "lastMonth",
}


def canonicalize_date_value(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return DEFAULT_DATE_VALUE
    if s in ALLOWED_DATE_VALUES:
        return s
    mapped = _DATE_VALUE_ALIASES.get(s.lower())
    if mapped:
        return mapped
    return DEFAULT_DATE_VALUE


def cycle_for_date_value(date_value: str) -> str:
    return DATE_VALUE_CYCLE.get(date_value, DEFAULT_CYCLE)


def _parse_workflow_id(raw: Any) -> Optional[int]:
    try:
        if raw is None or str(raw).strip() == "":
            return None
        return int(raw)
    except (TypeError, ValueError):
        return None


def _normalize_item(raw: Any) -> Dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    date_value = canonicalize_date_value(item.get("date_value"))
    cycle_raw = str(item.get("cycle") or "").strip().lower()
    cycle = cycle_for_date_value(date_value)
    # 若显式 cycle 与 dateValue 不一致，以 dateValue 为准（避免非法组合）
    if cycle_raw and cycle_raw != cycle:
        cycle = cycle_for_date_value(date_value)
    return {
        "depend_workflow_id": _parse_workflow_id(item.get("depend_workflow_id")),
        "cycle": cycle,
        "date_value": date_value,
    }


def normalize_dependent_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """归一化为 {relation, depend_items[]}；兼容旧单依赖字段。"""
    raw = params if isinstance(params, dict) else {}
    relation = str(raw.get("relation") or DEFAULT_RELATION).strip().upper()
    if relation not in ALLOWED_RELATIONS:
        relation = DEFAULT_RELATION

    items_raw = raw.get("depend_items")
    items: List[Dict[str, Any]] = []
    if isinstance(items_raw, list) and items_raw:
        for it in items_raw:
            items.append(_normalize_item(it))
    else:
        # 旧格式：顶层 depend_workflow_id / date_value / cycle
        items.append(
            _normalize_item(
                {
                    "depend_workflow_id": raw.get("depend_workflow_id"),
                    "date_value": raw.get("date_value"),
                    "cycle": raw.get("cycle"),
                }
            )
        )

    # 便于旧代码/诊断：保留首项扁平字段
    first = items[0] if items else _normalize_item({})
    return {
        "relation": relation,
        "depend_items": items,
        "depend_workflow_id": first.get("depend_workflow_id"),
        "cycle": first.get("cycle") or DEFAULT_CYCLE,
        "date_value": first.get("date_value") or DEFAULT_DATE_VALUE,
    }


def validate_dependent_node_params(
    db: Session,
    *,
    node: TaskNode,
    current_workflow_id: Optional[int] = None,
    require_published_target: bool = False,
) -> Dict[str, Any]:
    """校验 DEPENDENT 节点 params；返回规范化后的 params。"""
    if (node.node_type or "").upper() != "DEPENDENT":
        raise ValueError(f"节点 {node.id} 不是 DEPENDENT 类型")
    cfg = normalize_dependent_params(node.params if isinstance(node.params, dict) else {})
    items = cfg.get("depend_items") or []
    if not items:
        raise ValueError(f"DEPENDENT 节点「{node.name}」未配置依赖项")

    for i, item in enumerate(items):
        dep_id = item.get("depend_workflow_id")
        label = f"第 {i + 1} 条依赖"
        if not dep_id:
            raise ValueError(
                f"DEPENDENT 节点「{node.name}」{label}未配置 depend_workflow_id（请选择依赖的工作流）"
            )
        if current_workflow_id is not None and int(dep_id) == int(current_workflow_id):
            raise ValueError(f"DEPENDENT 节点「{node.name}」不能依赖当前工作流自身")

        dv = item.get("date_value")
        if dv not in ALLOWED_DATE_VALUES:
            raise ValueError(f"DEPENDENT 节点「{node.name}」{label} date_value 非法：{dv!r}")

        target = db.query(Workflow).filter(Workflow.id == int(dep_id)).first()
        if not target:
            raise ValueError(f"DEPENDENT 节点「{node.name}」{label}依赖的工作流不存在（id={dep_id}）")
        if int(target.workspace_id) != int(node.workspace_id):
            raise ValueError(f"DEPENDENT 节点「{node.name}」只能依赖同一工作空间内的工作流")
        if require_published_target:
            has_def = bool((target.scheduler_definition_id or "").strip())
            if not has_def:
                raise ValueError(
                    f"被依赖工作流「{target.name}」尚未发布到调度引擎；"
                    f"请先发布该工作流，再发布当前工作流"
                )
    return cfg


def validate_dag_dependent_nodes(
    db: Session,
    wf: Workflow,
    *,
    require_published_target: bool = False,
) -> None:
    dag = wf.dag_config or {}
    for n in dag.get("nodes") or []:
        nid = n.get("node_id")
        if nid is None:
            continue
        node = db.query(TaskNode).filter(TaskNode.id == int(nid)).first()
        if not node or (node.node_type or "").upper() != "DEPENDENT":
            continue
        validate_dependent_node_params(
            db,
            node=node,
            current_workflow_id=int(wf.id) if wf.id else None,
            require_published_target=require_published_target,
        )


def build_dependent_task_params(
    *,
    project_code: int,
    items: Optional[List[Dict[str, Any]]] = None,
    relation: str = DEFAULT_RELATION,
    # 兼容旧单依赖调用
    definition_code: Optional[int] = None,
    cycle: str = DEFAULT_CYCLE,
    date_value: str = DEFAULT_DATE_VALUE,
) -> Dict[str, Any]:
    """组装 DolphinScheduler DEPENDENT taskParams（整流程 depTaskCode=0）。"""
    rel = str(relation or DEFAULT_RELATION).strip().upper()
    if rel not in ALLOWED_RELATIONS:
        rel = DEFAULT_RELATION

    depend_item_list: List[Dict[str, Any]] = []
    if items:
        for it in items:
            dv = canonicalize_date_value(it.get("date_value"))
            depend_item_list.append(
                {
                    "projectCode": int(project_code),
                    "definitionCode": int(it["definition_code"]),
                    "depTaskCode": 0,
                    "cycle": cycle_for_date_value(dv),
                    "dateValue": dv,
                }
            )
    elif definition_code is not None:
        dv = canonicalize_date_value(date_value)
        depend_item_list.append(
            {
                "projectCode": int(project_code),
                "definitionCode": int(definition_code),
                "depTaskCode": 0,
                "cycle": cycle_for_date_value(dv) if not cycle else (cycle or cycle_for_date_value(dv)),
                "dateValue": dv,
            }
        )
        # 若显式传入 cycle，优先用显式（旧测试）
        if cycle:
            depend_item_list[-1]["cycle"] = cycle or DEFAULT_CYCLE
    else:
        raise ValueError("build_dependent_task_params 需要 items 或 definition_code")

    return {
        "localParams": [],
        "resourceList": [],
        "dependence": {
            "relation": "AND",
            "dependTaskList": [
                {
                    "relation": rel,
                    "dependItemList": depend_item_list,
                }
            ],
        },
        "conditionResult": {"successNode": [""], "failedNode": [""]},
        "waitStartTimeout": {},
    }


def resolve_business_date(date_value: str, *, now: Optional[datetime] = None) -> str:
    """兼容旧接口：today / yesterday → YYYY-MM-DD。"""
    base = now or datetime.now()
    dv = canonicalize_date_value(date_value)
    if dv == "yesterday":
        return (base - timedelta(days=1)).strftime("%Y-%m-%d")
    return base.strftime("%Y-%m-%d")


def _business_dates_for_item(date_value: str, *, now: Optional[datetime] = None) -> List[str]:
    """本地检查用：将 dateValue 近似为业务日集合（与 DS 可能有偏差）。"""
    base = now or datetime.now()
    dv = canonicalize_date_value(date_value)
    today = base.date()

    def fmt(d) -> str:
        return d.strftime("%Y-%m-%d")

    if dv == "today":
        return [fmt(today)]
    if dv == "yesterday":
        return [fmt(today - timedelta(days=1))]
    if dv == "last1Days":
        return [fmt(today - timedelta(days=1))]
    if dv == "last2Days":
        return [fmt(today - timedelta(days=i)) for i in range(1, 3)]
    if dv == "last3Days":
        return [fmt(today - timedelta(days=i)) for i in range(1, 4)]
    if dv == "last7Days":
        return [fmt(today - timedelta(days=i)) for i in range(1, 8)]
    if dv in ("currentHour", "last1Hour", "last2Hours", "last3Hours", "last24Hours", "thisWeek", "lastWeek", "thisMonth", "lastMonth"):
        # 小时/周/月：本地用「当天业务日有一条 success」作近似
        return [fmt(today)]
    return [fmt(today)]


def _item_success_local(
    db: Session,
    *,
    workflow_id: int,
    date_value: str,
    business_date: Optional[str],
    now: Optional[datetime] = None,
) -> bool:
    if (business_date or "").strip():
        dates = [business_date.strip()]
    else:
        dates = _business_dates_for_item(date_value, now=now)
    return (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == workflow_id,
            WorkflowInstance.business_date.in_(dates),
            WorkflowInstance.status == "success",
        )
        .first()
        is not None
    )


def check_dependent_local(
    db: Session,
    node: TaskNode,
    *,
    business_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, List[str]]:
    """本地执行：按 relation 聚合多依赖项；时间窗为近似实现。"""
    logs: List[str] = []
    cfg = validate_dependent_node_params(db, node=node, require_published_target=False)
    relation = cfg.get("relation") or DEFAULT_RELATION
    items = cfg.get("depend_items") or []
    logs.append(
        f"[INFO] DEPENDENT 检查 {len(items)} 条依赖（relation={relation}）；"
        f"本地时间窗为近似，生产以 Dolphin 窗口内最近成功实例为准"
    )

    results: List[bool] = []
    for i, item in enumerate(items):
        dep_id = int(item["depend_workflow_id"])
        target = db.query(Workflow).filter(Workflow.id == dep_id).first()
        assert target is not None
        dv = item["date_value"]
        ok = _item_success_local(
            db,
            workflow_id=dep_id,
            date_value=dv,
            business_date=business_date,
            now=now,
        )
        results.append(ok)
        dates = [business_date.strip()] if (business_date or "").strip() else _business_dates_for_item(dv, now=now)
        if ok:
            logs.append(
                f"[INFO] 依赖[{i + 1}] 工作流「{target.name}」#{target.id} "
                f"date_value={dv} dates={dates} → 通过"
            )
        else:
            logs.append(
                f"[ERROR] 依赖[{i + 1}] 未找到工作流「{target.name}」在 {dates} 的成功实例"
            )

    if relation == "OR":
        passed = any(results) if results else False
    else:
        passed = all(results) if results else False

    if passed:
        logs.append("[INFO] DEPENDENT 依赖通过")
        return True, logs
    logs.append("[ERROR] DEPENDENT 依赖未满足")
    return False, logs

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""
运行诊断：回答运维最常问的「这次为什么没跑 / 为什么还没跑完」。

判定只用 GIDO 自己的事实（工作流状态、运行台账、节点实例、跨工作流依赖、采集健康度），
不向执行引擎发请求，所以基线告警里可以直接带上结论，不必等人去引擎界面翻。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.workspace import (
    NodeInstance,
    TaskNode,
    Workflow,
    WorkflowInstance,
    Workspace,
)

logger = logging.getLogger(__name__)

# 诊断结论的严重度，前端按它排序与着色
LEVELS = ("blocker", "warning", "info", "ok")
_LEVEL_ORDER = {lvl: i for i, lvl in enumerate(LEVELS)}

# 运行超过这个时长还没结束，值得单独提一句
_LONG_RUN_HOURS = 6


def _finding(code: str, level: str, title: str, detail: str, action: Optional[str] = None) -> Dict[str, Any]:
    return {"code": code, "level": level, "title": title, "detail": detail, "action": action}


def _workspace_tz(db: Session, workspace_id: int) -> ZoneInfo:
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    try:
        return ZoneInfo(getattr(ws, "timezone", None) or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _schedule_findings(db: Session, wf: Workflow, tz: ZoneInfo) -> List[Dict[str, Any]]:
    """调度侧的硬性阻塞：没上线、被暂停、没配周期，这三种情况实例根本不会产生。"""
    out: List[Dict[str, Any]] = []
    status = (getattr(wf, "status", None) or "").strip()
    if status == "paused":
        out.append(_finding(
            "schedule_paused", "blocker", "周期调度已暂停",
            "工作流处于「已暂停」状态，生产定义还在，但不会再按周期自动触发。",
            "在工作流页面点「恢复调度」；只补这一次的话用补数据。",
        ))
    elif status == "offline":
        out.append(_finding(
            "workflow_offline", "blocker", "工作流已下线",
            "工作流已下线，生产调度上没有生效的定义。",
            "重新发布上线后才会产生周期实例。",
        ))
    elif status != "published":
        out.append(_finding(
            "not_published", "blocker", "工作流未发布",
            f"当前状态为「{status or '未知'}」，还没有生产版本。",
            "发布上线后才会进入周期调度。",
        ))

    if (getattr(wf, "schedule_type", None) or "") != "cron":
        out.append(_finding(
            "no_cron_schedule", "blocker", "未配置周期调度",
            f"调度方式为「{getattr(wf, 'schedule_type', None) or '手动'}」，不会自动触发，只能手动运行或补数据。",
            "需要每天自动跑的话，在工作流配置里改为周期调度并填 Cron。",
        ))
    elif not (getattr(wf, "cron_expression", None) or "").strip():
        out.append(_finding(
            "cron_missing", "blocker", "周期调度缺少 Cron 表达式",
            "调度方式是周期调度，但 Cron 表达式为空。",
            "补上 Cron 后重新发布。",
        ))
    return out


def _next_run_hint(wf: Workflow, tz: ZoneInfo, now_local: datetime) -> Optional[Dict[str, Any]]:
    cron = (getattr(wf, "cron_expression", None) or "").strip()
    if not cron or (getattr(wf, "schedule_type", None) or "") != "cron":
        return None
    try:
        from app.services.cron_utils import preview_next_runs

        _linux, _quartz, times = preview_next_runs(cron, count=1, timezone_id=str(tz), base=now_local)
    except Exception:
        logger.debug("诊断预览下次调度时间失败 wf_id=%s", getattr(wf, "id", None), exc_info=True)
        return None
    if not times:
        return None
    return _finding(
        "next_run", "info", "下次调度时间",
        f"按 Cron「{cron}」，下一次自动触发是 {times[0]}（{tz}）。",
    )


def _dependency_findings(
    db: Session, wf: Workflow, business_date: Optional[str], now: datetime
) -> List[Dict[str, Any]]:
    """跨工作流依赖（DEPENDENT 节点）有没有满足——「卡在上游」最常见的形态。"""
    from app.services.workflow_dependent import check_dependent_local

    node_ids = [
        n.get("node_id")
        for n in ((getattr(wf, "dag_config", None) or {}).get("nodes") or [])
        if n.get("node_id")
    ]
    if not node_ids:
        return []
    nodes = (
        db.query(TaskNode)
        .filter(TaskNode.id.in_([int(i) for i in node_ids]))
        .all()
    )
    out: List[Dict[str, Any]] = []
    for node in nodes:
        if (node.node_type or "").upper() != "DEPENDENT":
            continue
        try:
            passed, logs = check_dependent_local(db, node, business_date=business_date, now=now)
        except Exception as e:
            # 依赖配置本身有问题也是一条有用的结论
            out.append(_finding(
                "dependency_invalid", "warning", f"依赖节点「{node.name}」配置异常",
                str(e), "到工作流里检查这个依赖节点的配置。",
            ))
            continue
        if passed:
            continue
        unmet = [ln for ln in logs if ln.startswith("[ERROR]")]
        out.append(_finding(
            "dependency_unmet", "blocker", f"在等上游：依赖节点「{node.name}」未满足",
            "；".join(unmet) or "跨工作流依赖尚未满足。",
            "先把上游工作流跑成功（或对上游补数据），本工作流会自行继续。",
        ))
    return out


def _instance_findings(
    db: Session, inst: WorkflowInstance, now: datetime
) -> List[Dict[str, Any]]:
    """已经有实例时，解释它停在哪。"""
    out: List[Dict[str, Any]] = []
    status = (inst.status or "").strip()
    if getattr(inst, "status_override", None):
        out.append(_finding(
            "status_override", "info", "状态由人工设定",
            f"实例 #{inst.id} 被人工置为「{inst.status_override}」"
            f"{f'：{inst.override_reason}' if inst.override_reason else ''}。调度采集不再改动它的状态。",
        ))

    nodes = db.query(NodeInstance).filter(NodeInstance.workflow_instance_id == inst.id).all()
    node_names = {}
    if nodes:
        ids = [n.node_id for n in nodes if n.node_id]
        if ids:
            node_names = {
                t.id: t.name for t in db.query(TaskNode).filter(TaskNode.id.in_(ids)).all()
            }

    def names_of(rows) -> str:
        return "、".join(node_names.get(r.node_id, f"节点#{r.node_id}") for r in rows) or "（无节点明细）"

    if status == "success":
        out.append(_finding(
            "instance_success", "ok", "这次已经跑成功了",
            f"实例 #{inst.id} 状态为成功"
            f"{f'，结束于 {inst.finished_at}' if inst.finished_at else ''}。"
            "如果下游还在等，多半是下游自己的依赖窗口没对上。",
        ))
        return out

    if status == "failed":
        failed = [n for n in nodes if n.status == "failed"]
        out.append(_finding(
            "instance_failed", "blocker", "这次跑了但失败了",
            f"实例 #{inst.id} 失败" + (f"，失败节点：{names_of(failed)}" if failed else "，暂无失败节点明细"),
            "看失败节点日志；数据已旁路修复的话可以对实例「置成功」放行下游。",
        ))
        return out

    if status in ("running", "pending"):
        running = [n for n in nodes if n.status == "running"]
        waiting = [n for n in nodes if n.status == "pending"]
        detail_parts = [f"实例 #{inst.id} 当前为「{status}」"]
        if running:
            detail_parts.append(f"运行中节点：{names_of(running)}")
        if waiting:
            detail_parts.append(f"等待中节点：{names_of(waiting)}")
        if not nodes:
            detail_parts.append("还没有任何节点开始，通常是刚被触发或在等资源/等上游")
        out.append(_finding(
            "instance_in_progress", "info", "这次还在跑",
            "；".join(detail_parts),
        ))
        started = inst.started_at
        if started and now - started > timedelta(hours=_LONG_RUN_HOURS):
            hours = int((now - started).total_seconds() // 3600)
            out.append(_finding(
                "instance_long_running", "warning", "已经跑了很久",
                f"实例 #{inst.id} 从 {started} 起已运行约 {hours} 小时，明显超出日常水平。",
                "配了运行超时基线的话会自动告警；否则建议看节点日志确认是否卡住。",
            ))
        return out

    if status == "killed":
        out.append(_finding(
            "instance_killed", "warning", "这次被终止了",
            f"实例 #{inst.id} 状态为已终止，不会自己继续。",
            "确认是人为终止还是引擎侧中断，然后重跑。",
        ))
        return out

    out.append(_finding(
        "instance_unknown_status", "warning", f"实例状态为「{status or '未知'}」",
        f"实例 #{inst.id} 的状态无法归类，可能是采集中间态。",
    ))
    return out


def diagnose_workflow_run(
    db: Session,
    *,
    workflow: Workflow,
    business_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    诊断某工作流在某业务日期上的运行情况。
    business_date 为空时取最近一条实例的业务日期，没有实例则取工作空间当天。
    """
    now_utc = now or datetime.utcnow()
    tz = _workspace_tz(db, int(workflow.workspace_id))
    now_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    biz = (business_date or "").strip() or None
    if biz is None:
        latest = (
            db.query(WorkflowInstance)
            .filter(WorkflowInstance.workflow_id == workflow.id)
            .order_by(desc(WorkflowInstance.id))
            .first()
        )
        biz = (getattr(latest, "business_date", None) or "").strip() or now_local.strftime("%Y-%m-%d")

    instances = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == workflow.id,
            WorkflowInstance.business_date == biz,
        )
        .order_by(desc(WorkflowInstance.id))
        .all()
    )

    findings: List[Dict[str, Any]] = []
    findings.extend(_schedule_findings(db, workflow, tz))

    if instances:
        # 同一业务日期可能有周期 + 重跑多条；成功过就以成功为准，否则看最近一条
        success = next((i for i in instances if (i.status or "") == "success"), None)
        findings.extend(_instance_findings(db, success or instances[0], now_utc))
        if len(instances) > 1:
            findings.append(_finding(
                "multiple_runs", "info", f"这个业务日期有 {len(instances)} 次运行",
                "实例号：" + "、".join(f"#{i.id}({i.status})" for i in instances[:8]),
            ))
    else:
        findings.append(_finding(
            "no_instance", "blocker", "这个业务日期没有任何运行记录",
            f"业务日期 {biz} 下查不到实例，说明它压根没被触发，或者触发了但 GIDO 还没采集到。",
        ))
        hint = _next_run_hint(workflow, tz, now_local)
        if hint:
            findings.append(hint)

    # 依赖只在「没跑」或「还没跑完」时才值得查：已经成功了再报上游未满足只会误导
    if not any(f["code"] == "instance_success" for f in findings):
        findings.extend(_dependency_findings(db, workflow, biz, now_utc))

    findings.extend(_collector_findings(db))

    if not any(f["level"] in ("blocker", "warning") for f in findings):
        findings.append(_finding(
            "no_blocker_found", "ok", "没发现平台侧阻塞",
            "调度配置、运行台账、上游依赖和采集健康度都正常。如果业务方仍认为数据没到，"
            "建议核对下游的依赖窗口口径。",
        ))

    findings.sort(key=lambda f: _LEVEL_ORDER.get(f["level"], 99))
    return {
        "workflow_id": int(workflow.id),
        "workflow_name": workflow.name,
        "business_date": biz,
        "instance_ids": [int(i.id) for i in instances],
        "verdict": findings[0]["title"] if findings else "无结论",
        "findings": findings,
    }


def _collector_findings(db: Session) -> List[Dict[str, Any]]:
    """采集落后时，「没有实例」可能只是还没看到，得说清楚免得误判。"""
    try:
        from app.services.run_collector import collector_health

        health = collector_health(db)
    except Exception:
        logger.debug("诊断读取采集健康度失败", exc_info=True)
        return []
    if not health.get("enabled"):
        return [_finding(
            "collector_disabled", "warning", "生产调度未启用",
            "当前工作空间没有启用生产调度引擎，不会有周期实例产生。",
            "到平台集成配置里启用并完成绑定。",
        )]
    if health.get("stale"):
        lag = health.get("lag_seconds")
        return [_finding(
            "collector_stale", "warning", "运行数据采集落后",
            f"最近一次成功采集距今约 {int(lag)} 秒" if isinstance(lag, (int, float))
            else "采集从未成功过",
            "实例可能已经在跑，只是还没同步进来；先看采集状态再判断是否真的没跑。",
        )]
    return []

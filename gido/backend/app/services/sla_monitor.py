# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-09-11
"""
基线巡检：承诺完成时间（未按时出数据）与最长运行时长（跑太久）。

失败告警只能覆盖「跑了并且红了」。生产上更隐蔽的事故是：
- 到了承诺时间，这个业务日期压根没有成功的运行（卡上游、没被调度起来、被人停了）
- 任务还在跑，但已经远超正常时长，下游马上要等不到数据

两类都读 GIDO 自己的实例事实表判断，与执行引擎无关。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.workspace import (
    Workflow,
    WorkflowInstance,
    WorkflowSlaRule,
    Workspace,
)
from app.services.alert_center import open_instance_alert, open_workflow_alert, resolve_alerts

logger = logging.getLogger(__name__)

# 承诺时间过了多久还没成功就不再补告警：避免首次启用基线时把历史日期全部刷出来
_MISS_LOOKBACK_HOURS = 30


def _tz(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Shanghai")
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _parse_hhmm(raw: Optional[str]) -> Optional[tuple[int, int]]:
    if not raw or ":" not in str(raw):
        return None
    hh, _, mm = str(raw).partition(":")
    try:
        h, m = int(hh), int(mm)
    except ValueError:
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h, m
    return None


def _deadline_utc(business_date: str, hhmm: tuple[int, int], offset_days: int, tz: ZoneInfo) -> Optional[datetime]:
    """业务日期 D 的承诺时间点（UTC naive，与实例时间同一口径）。"""
    try:
        base = datetime.strptime(business_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    local = base.replace(hour=hhmm[0], minute=hhmm[1], tzinfo=tz) + timedelta(days=int(offset_days))
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _due_business_dates(now_utc: datetime, hhmm: tuple[int, int], offset_days: int, tz: ZoneInfo) -> list[str]:
    """承诺时间已过、且仍在回溯窗口内的业务日期（通常就 1~2 天）。"""
    dates: list[str] = []
    today_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    for back in range(0, 4):
        biz = (today_local - timedelta(days=int(offset_days) + back)).strftime("%Y-%m-%d")
        deadline = _deadline_utc(biz, hhmm, offset_days, tz)
        if deadline is None or deadline > now_utc:
            continue
        if now_utc - deadline > timedelta(hours=_MISS_LOOKBACK_HOURS):
            continue
        dates.append(biz)
    return dates


def _sla_dedupe_key(workflow_id: int, business_date: str) -> str:
    return f"sla:workflow:{workflow_id}:biz:{business_date}"


def _diagnosis_suffix(db: Session, wf: Workflow, business_date: str, now_utc: datetime) -> str:
    """
    基线告警里直接带上诊断结论：值班的人看到卡片就知道是卡在上游、被暂停，还是真的挂了，
    不用再登进来一层层点。诊断失败不能拖累告警本身。
    """
    try:
        from app.services.run_diagnosis import diagnose_workflow_run

        result = diagnose_workflow_run(db, workflow=wf, business_date=business_date, now=now_utc)
    except Exception:
        logger.warning("基线告警附带诊断失败 wf_id=%s", getattr(wf, "id", None), exc_info=True)
        return ""
    blockers = [f for f in result.get("findings") or [] if f.get("level") == "blocker"]
    if not blockers:
        return ""
    top = blockers[0]
    suffix = f" 诊断：{top.get('title')}——{top.get('detail')}"
    if top.get("action"):
        suffix += f" 建议：{top['action']}"
    return suffix


def _check_missed_deadline(db: Session, rule: WorkflowSlaRule, wf: Workflow, now_utc: datetime, tz: ZoneInfo) -> int:
    hhmm = _parse_hhmm(rule.expect_finish_time)
    if hhmm is None:
        return 0
    opened = 0
    for biz in _due_business_dates(now_utc, hhmm, rule.expect_finish_offset_days or 0, tz):
        key = _sla_dedupe_key(wf.id, biz)
        succeeded = (
            db.query(WorkflowInstance)
            .filter(
                WorkflowInstance.workflow_id == wf.id,
                WorkflowInstance.business_date == biz,
                WorkflowInstance.status == "success",
            )
            .first()
        )
        if succeeded is not None:
            # 晚到也算到了：关掉破线告警，不再打扰值班
            resolve_alerts(db, dedupe_key=key, alert_types=("sla",))
            continue
        running = (
            db.query(WorkflowInstance)
            .filter(
                WorkflowInstance.workflow_id == wf.id,
                WorkflowInstance.business_date == biz,
                WorkflowInstance.status.in_(("running", "pending")),
            )
            .first()
        )
        state = "仍在运行中" if running is not None else "没有成功的运行"
        deadline_local = _deadline_utc(biz, hhmm, rule.expect_finish_offset_days or 0, tz)
        deadline_txt = (
            deadline_local.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%Y-%m-%d %H:%M")
            if deadline_local
            else rule.expect_finish_time
        )
        event = open_workflow_alert(
            db,
            workflow=wf,
            dedupe_key=key,
            alert_type="sla",
            level=rule.level or "error",
            message=(
                f"工作流 {wf.name} 未按时完成：业务日期 {biz} 承诺 {deadline_txt} 前产出，"
                f"当前{state}。" + _diagnosis_suffix(db, wf, biz, now_utc)
            ),
        )
        if event is not None:
            opened += 1
    return opened


def _check_long_running(db: Session, rule: WorkflowSlaRule, wf: Workflow, now_utc: datetime) -> int:
    limit = int(rule.max_duration_minutes or 0)
    if limit <= 0:
        return 0
    cutoff = now_utc - timedelta(minutes=limit)
    slow = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.workflow_id == wf.id,
            WorkflowInstance.status == "running",
            WorkflowInstance.started_at.isnot(None),
            WorkflowInstance.started_at < cutoff,
        )
        .all()
    )
    opened = 0
    for inst in slow:
        ran_minutes = int((now_utc - inst.started_at).total_seconds() // 60)
        event = open_instance_alert(
            db,
            workflow_instance=inst,
            alert_type="timeout",
            level="warning",
            message=(
                f"工作流 {wf.name} 运行超时：实例 #{inst.id} 已运行 {ran_minutes} 分钟，"
                f"超过基线 {limit} 分钟。"
            ),
        )
        if event is not None:
            opened += 1
    return opened


def evaluate_sla(db: Session, *, workspace_id: Optional[int] = None) -> dict:
    """巡检一轮基线。由后台任务驱动，不依赖用户打开页面。"""
    now_utc = datetime.utcnow()
    rules_q = db.query(WorkflowSlaRule).filter(WorkflowSlaRule.enabled.is_(True))
    if workspace_id is not None:
        rules_q = rules_q.filter(WorkflowSlaRule.workspace_id == int(workspace_id))
    rules = rules_q.all()

    missed = 0
    slow = 0
    checked = 0
    for rule in rules:
        wf = db.query(Workflow).filter(Workflow.id == rule.workflow_id).first()
        if wf is None or not wf.is_active:
            continue
        ws = db.query(Workspace).filter(Workspace.id == wf.workspace_id).first()
        tz = _tz(getattr(ws, "timezone", None))
        checked += 1
        try:
            missed += _check_missed_deadline(db, rule, wf, now_utc, tz)
            slow += _check_long_running(db, rule, wf, now_utc)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("基线巡检失败 workflow_id=%s: %s", rule.workflow_id, e, exc_info=True)
    return {"rules_checked": checked, "missed_deadline": missed, "long_running": slow}

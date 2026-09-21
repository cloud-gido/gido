# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core import perm_codes as PC
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.workspace import MetaTable, QualityCheckRecord, QualityRule, User
from app.services.cron_utils import assert_linux_cron
from app.services.quality_check import (
    SEVERITY_WARN,
    eval_threshold,
    normalize_severity,
    run_rule_check,
    run_table_checks,
    validate_rule_type,
)
from app.services.rbac import assert_workspace_data_capability, require_meta_table, require_quality_rule

router = APIRouter(prefix="/quality", tags=["数据质量"])


class RuleCreate(BaseModel):
    workspace_id: int
    table_id: int
    rule_name: str
    rule_type: str
    rule_config: Optional[Dict[str, Any]] = None
    threshold: Optional[str] = None
    severity: Optional[str] = SEVERITY_WARN
    schedule_cron: Optional[str] = None
    dolphin_refs: Optional[Dict[str, Any]] = None


class RuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[Dict[str, Any]] = None
    threshold: Optional[str] = None
    severity: Optional[str] = None
    is_active: Optional[bool] = None
    schedule_cron: Optional[str] = None
    dolphin_refs: Optional[Dict[str, Any]] = None


class CheckOptions(BaseModel):
    bizdate: Optional[str] = None
    notify: Optional[bool] = True


def _normalize_cron(raw: Optional[str]) -> Optional[str]:
    text_cron = (raw or "").strip()
    if not text_cron:
        return None
    try:
        return assert_linux_cron(text_cron)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _reload_quality_jobs() -> None:
    try:
        from app.services.scheduler import reload_quality_schedules

        reload_quality_schedules()
    except Exception:
        pass


def _serialize_rule(db: Session, rule: QualityRule) -> Dict[str, Any]:
    table = db.query(MetaTable).filter(MetaTable.id == rule.table_id).first()
    latest = (
        db.query(QualityCheckRecord)
        .filter(QualityCheckRecord.rule_id == rule.id)
        .order_by(QualityCheckRecord.id.desc())
        .first()
    )
    return {
        "id": rule.id,
        "workspace_id": rule.workspace_id,
        "table_id": rule.table_id,
        "table_name": table.table_name if table else None,
        "catalog": (table.db_name if table else None) or None,
        "qualified_name": (
            f"{table.db_name}.{table.table_name}" if table and table.db_name else (table.table_name if table else None)
        ),
        "rule_name": rule.rule_name,
        "rule_type": rule.rule_type,
        "rule_config": rule.rule_config,
        "threshold": rule.threshold,
        "severity": getattr(rule, "severity", None) or SEVERITY_WARN,
        "schedule_cron": getattr(rule, "schedule_cron", None),
        "is_active": bool(rule.is_active),
        "dolphin_refs": rule.dolphin_refs,
        "created_at": rule.created_at,
        "latest_status": latest.status if latest else None,
        "latest_score": latest.score if latest else None,
        "latest_checked_at": latest.checked_at if latest else None,
    }


def _serialize_record(record: QualityCheckRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "rule_id": record.rule_id,
        "status": record.status,
        "score": record.score,
        "detail": record.detail,
        "checked_at": record.checked_at,
    }


@router.get("/rules")
def list_rules(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    rules = (
        db.query(QualityRule)
        .filter(QualityRule.workspace_id == workspace_id)
        .order_by(QualityRule.id.desc())
        .all()
    )
    return [_serialize_rule(db, rule) for rule in rules]


@router.post("/rules")
def create_rule(rule_in: RuleCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, rule_in.workspace_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    table = require_meta_table(db, current_user, rule_in.table_id)
    if table.workspace_id != rule_in.workspace_id:
        raise HTTPException(status_code=400, detail="元数据表与工作空间不一致")
    rule_type = validate_rule_type(rule_in.rule_type)
    name = (rule_in.rule_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="规则名称不能为空")
    rule = QualityRule(
        workspace_id=rule_in.workspace_id,
        table_id=rule_in.table_id,
        rule_name=name[:128],
        rule_type=rule_type,
        rule_config=rule_in.rule_config,
        threshold=(rule_in.threshold or ">=95").strip()[:32],
        severity=normalize_severity(rule_in.severity),
        schedule_cron=_normalize_cron(rule_in.schedule_cron),
        dolphin_refs=rule_in.dolphin_refs,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    _reload_quality_jobs()
    return _serialize_rule(db, rule)


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    body: RuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = require_quality_rule(db, current_user, rule_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    fields = body.model_fields_set
    if "rule_name" in fields:
        name = (body.rule_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="规则名称不能为空")
        rule.rule_name = name[:128]
    if "rule_type" in fields:
        rule.rule_type = validate_rule_type(body.rule_type or "")
    if "rule_config" in fields:
        rule.rule_config = body.rule_config
    if "threshold" in fields:
        rule.threshold = ((body.threshold or ">=0").strip())[:32]
    if "severity" in fields:
        rule.severity = normalize_severity(body.severity)
    if "is_active" in fields:
        rule.is_active = bool(body.is_active)
    if "schedule_cron" in fields:
        rule.schedule_cron = _normalize_cron(body.schedule_cron)
    if "dolphin_refs" in fields:
        rule.dolphin_refs = body.dolphin_refs
    db.commit()
    db.refresh(rule)
    _reload_quality_jobs()
    return _serialize_rule(db, rule)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = require_quality_rule(db, current_user, rule_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    from sqlalchemy.exc import IntegrityError

    db.query(QualityCheckRecord).filter(QualityCheckRecord.rule_id == rule_id).delete(synchronize_session=False)
    try:
        db.delete(rule)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="规则仍被引用，无法删除。可先停用该规则。") from e
    _reload_quality_jobs()
    return {"message": "删除成功"}


@router.post("/rules/{rule_id}/check")
def run_check(
    rule_id: int,
    body: Optional[CheckOptions] = None,
    bizdate: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rule = require_quality_rule(db, current_user, rule_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    opts = body or CheckOptions()
    return run_rule_check(
        db,
        rule,
        bizdate=opts.bizdate or bizdate,
        notify=True if opts.notify is None else bool(opts.notify),
        trigger="manual",
    )


@router.post("/tables/{table_id}/check")
def run_table_check(
    table_id: int,
    body: Optional[CheckOptions] = None,
    bizdate: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """一张表上全部启用规则。编排 HTTP 节点可据此读 should_block。"""
    table = require_meta_table(db, current_user, table_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    opts = body or CheckOptions()
    return run_table_checks(
        db,
        table.id,
        bizdate=opts.bizdate or bizdate,
        notify=True if opts.notify is None else bool(opts.notify),
        trigger="table",
    )


@router.get("/tables/{table_id}/dolphin-hook")
def table_dolphin_hook(
    table_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """给 Dolphin HTTP 节点用的检查挂钩说明。GIDO 执行检查，DS 负责触发与阻断。"""
    from app.services.alert_notification import gido_public_url

    table = require_meta_table(db, current_user, table_id)
    assert_workspace_data_capability(db, current_user, table.workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    base = (gido_public_url(db) or "").rstrip("/")
    path = f"/api/quality/tables/{table.id}/check"
    url = f"{base}{path}" if base else path
    return {
        "table_id": table.id,
        "method": "POST",
        "url": url,
        "path": path,
        "query": {"bizdate": "${system.biz.date}"},
        "auth_header": "Authorization: Bearer <GIDO_TOKEN> 或工作空间服务账号",
        "success_when": "HTTP 200 且 body.should_block == false（或 status 聚合无强失败）",
        "block_when": "body.should_block == true：请在 DS HTTP 节点用脚本断言失败以阻断下游",
        "curl": (
            f"curl -sS -X POST '{url}?bizdate=${{system.biz.date}}' "
            f"-H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json'"
        ),
        "note": (
            "与实例中心同一分工：GIDO 是质量规则与检查结果的事实来源；"
            "Dolphin 只负责按工作流 cron 触发本接口，并根据 should_block 决定是否失败断边。"
            "空间已启用 Dolphin 时，规则上的 schedule_cron 不会走本机 APScheduler，避免双调度。"
        ),
    }


@router.get("/rules/{rule_id}/dolphin-hook")
def rule_dolphin_hook(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.services.alert_notification import gido_public_url

    rule = require_quality_rule(db, current_user, rule_id)
    base = (gido_public_url(db) or "").rstrip("/")
    path = f"/api/quality/rules/{rule.id}/check"
    url = f"{base}{path}" if base else path
    return {
        "rule_id": rule.id,
        "method": "POST",
        "url": url,
        "path": path,
        "query": {"bizdate": "${system.biz.date}"},
        "auth_header": "Authorization: Bearer <GIDO_TOKEN>",
        "success_when": "HTTP 200 且 body.should_block == false",
        "block_when": "body.should_block == true",
        "curl": (
            f"curl -sS -X POST '{url}?bizdate=${{system.biz.date}}' "
            f"-H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json'"
        ),
        "note": "单规则检查。表级批量请用 /quality/tables/{table_id}/dolphin-hook。",
    }


@router.post("/internal/tables/{table_id}/check")
def internal_run_table_quality(
    table_id: int,
    bizdate: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """供 Dolphin QUALITY 节点回调；Bearer=INTERNAL_TOKEN。should_block 时 HTTP 409。"""
    token = (authorization or "").replace("Bearer ", "").strip()
    if not settings.INTERNAL_TOKEN or token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="无效的内部令牌")
    table = db.query(MetaTable).filter(MetaTable.id == table_id).first()
    if not table:
        raise HTTPException(status_code=404, detail="表不存在")
    result = run_table_checks(
        db,
        table_id,
        bizdate=bizdate,
        notify=True,
        trigger="dolphin",
    )
    if result.get("should_block"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "强规则失败，应阻断下游",
                "should_block": True,
                "fail": result.get("fail"),
                "results": result.get("results"),
            },
        )
    return result


@router.post("/internal/rules/{rule_id}/check")
def internal_run_rule_quality(
    rule_id: int,
    bizdate: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    token = (authorization or "").replace("Bearer ", "").strip()
    if not settings.INTERNAL_TOKEN or token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="无效的内部令牌")
    rule = db.query(QualityRule).filter(QualityRule.id == rule_id).first()
    if not rule or not rule.is_active:
        raise HTTPException(status_code=404, detail="规则不存在或已停用")
    result = run_rule_check(db, rule, bizdate=bizdate, notify=True, trigger="dolphin")
    if result.get("should_block"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "强规则失败，应阻断下游",
                "should_block": True,
                "result": result,
            },
        )
    return result


@router.get("/rules/{rule_id}/records")
def list_check_records(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_quality_rule(db, current_user, rule_id)
    records = (
        db.query(QualityCheckRecord)
        .filter(QualityCheckRecord.rule_id == rule_id)
        .order_by(QualityCheckRecord.id.desc())
        .limit(30)
        .all()
    )
    return [_serialize_record(r) for r in records]


@router.get("/dashboard")
def quality_dashboard(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    rules = db.query(QualityRule).filter(QualityRule.workspace_id == workspace_id).all()
    total_rules = len(rules)
    active_rules = sum(1 for r in rules if r.is_active)
    scheduled_rules = sum(1 for r in rules if r.is_active and getattr(r, "schedule_cron", None))
    pass_count = fail_count = warning_count = unchecked = 0
    failing: List[Dict[str, Any]] = []
    for rule in rules:
        latest = (
            db.query(QualityCheckRecord)
            .filter(QualityCheckRecord.rule_id == rule.id)
            .order_by(QualityCheckRecord.id.desc())
            .first()
        )
        if not latest:
            unchecked += 1
            continue
        if latest.status == "pass":
            pass_count += 1
        elif latest.status == "fail":
            fail_count += 1
            failing.append({
                "rule_id": rule.id,
                "rule_name": rule.rule_name,
                "score": latest.score,
                "checked_at": latest.checked_at,
                "severity": getattr(rule, "severity", None) or SEVERITY_WARN,
            })
        else:
            warning_count += 1
    failing.sort(key=lambda row: (row.get("score") is None, row.get("score") or 0))
    checked = pass_count + fail_count + warning_count
    return {
        "total_rules": total_rules,
        "active_rules": active_rules,
        "scheduled_rules": scheduled_rules,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "unchecked": unchecked,
        "pass_rate": f"{int(pass_count / checked * 100)}%" if checked > 0 else "N/A",
        "top_failures": failing[:8],
    }


@router.get("/rules/{rule_id}/trend")
def get_quality_trend(rule_id: int, days: int = 30, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_quality_rule(db, current_user, rule_id)
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))
    records = (
        db.query(QualityCheckRecord)
        .filter(QualityCheckRecord.rule_id == rule_id, QualityCheckRecord.checked_at >= since)
        .order_by(QualityCheckRecord.checked_at)
        .all()
    )
    return {
        "rule_id": rule_id,
        "trend": [
            {
                "date": r.checked_at.strftime("%Y-%m-%d %H:%M") if r.checked_at else "",
                "score": r.score,
                "status": r.status,
            }
            for r in records
        ],
    }


@router.get("/workspace-trend")
def get_workspace_quality_trend(
    workspace_id: int,
    days: int = 7,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    result = []
    span = max(1, min(days, 30))
    for i in range(span - 1, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).date()
        day_start = datetime.combine(day, datetime.min.time())
        day_end = datetime.combine(day, datetime.max.time())
        rules = db.query(QualityRule).filter(QualityRule.workspace_id == workspace_id).all()
        rule_ids = [r.id for r in rules]
        if not rule_ids:
            result.append({"date": str(day), "pass": 0, "fail": 0, "avg_score": 0})
            continue
        records = db.query(QualityCheckRecord).filter(
            QualityCheckRecord.rule_id.in_(rule_ids),
            QualityCheckRecord.checked_at >= day_start,
            QualityCheckRecord.checked_at <= day_end,
        ).all()
        pass_c = sum(1 for r in records if r.status == "pass")
        fail_c = sum(1 for r in records if r.status == "fail")
        avg = int(sum(r.score or 0 for r in records) / len(records)) if records else 0
        result.append({"date": str(day), "pass": pass_c, "fail": fail_c, "avg_score": avg})
    return result


def migrate_quality_rule_severity(engine) -> None:
    """质量规则强弱校验与定时字段。"""
    insp = inspect(engine)
    if not insp.has_table("dw_quality_rules"):
        return
    cols = {c["name"] for c in insp.get_columns("dw_quality_rules")}
    with engine.begin() as conn:
        if "severity" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN severity VARCHAR(16) NULL DEFAULT 'warn'"))
            else:
                conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN severity VARCHAR(16) DEFAULT 'warn'"))
        if "schedule_cron" not in cols:
            if engine.dialect.name == "mysql":
                conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN schedule_cron VARCHAR(64) NULL"))
            else:
                conn.execute(text("ALTER TABLE dw_quality_rules ADD COLUMN schedule_cron VARCHAR(64)"))


# 兼容旧测试与外部直接引用
_eval_threshold = eval_threshold

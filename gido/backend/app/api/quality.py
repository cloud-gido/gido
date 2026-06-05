# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import QualityRule, QualityCheckRecord, MetaTable, DataSource, User
from app.services.rbac import assert_workspace_data_capability, require_meta_table, require_quality_rule

router = APIRouter(prefix="/quality", tags=["数据质量"])


class RuleCreate(BaseModel):
    workspace_id: int
    table_id: int
    rule_name: str
    rule_type: str  # completeness/uniqueness/accuracy/timeliness/custom_sql/… 及 Dolphin 对齐类型
    rule_config: Optional[Dict[str, Any]] = None
    threshold: Optional[str] = None
    # 与 Dolphin 质量任务联动：如 {"process_code","task_code","definition"} 或 DS 规则 JSON 镜像
    dolphin_refs: Optional[Dict[str, Any]] = None


class RuleOut(BaseModel):
    id: int
    workspace_id: int
    table_id: int
    rule_name: str
    rule_type: str
    rule_config: Optional[Dict[str, Any]]
    threshold: Optional[str]
    is_active: bool
    dolphin_refs: Optional[Dict[str, Any]] = None
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("/rules", response_model=List[RuleOut])
def list_rules(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    return db.query(QualityRule).filter(QualityRule.workspace_id == workspace_id).all()


@router.post("/rules", response_model=RuleOut)
def create_rule(rule_in: RuleCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, rule_in.workspace_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    table = require_meta_table(db, current_user, rule_in.table_id)
    if table.workspace_id != rule_in.workspace_id:
        raise HTTPException(status_code=400, detail="元数据表与工作空间不一致")
    payload = rule_in.model_dump()
    rule = QualityRule(**payload, created_by=current_user.id)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = db.query(QualityRule).filter(QualityRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    assert_workspace_data_capability(db, current_user, rule.workspace_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)
    db.delete(rule)
    db.commit()
    return {"message": "删除成功"}


@router.post("/rules/{rule_id}/check")
def run_check(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.services.alert import alert_quality_failed
    rule = db.query(QualityRule).filter(QualityRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="规则不存在")
    assert_workspace_data_capability(db, current_user, rule.workspace_id, "developer", PC.GIDO_BATCH_QUALITY_WRITE)

    table = db.query(MetaTable).filter(MetaTable.id == rule.table_id).first()
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first() if table else None

    score, detail, status = 0, {}, "fail"
    try:
        score, detail = _execute_check(rule, table, ds)
        threshold = rule.threshold or ">=0"
        status = _eval_threshold(score, threshold)
    except Exception as e:
        detail = {"error": str(e)}

    record = QualityCheckRecord(rule_id=rule_id, status=status, score=score, detail=detail)
    db.add(record)
    db.commit()
    db.refresh(record)

    if status == "fail":
        alert_quality_failed(rule.rule_name, score, rule.threshold or ">=0")

    return {"record_id": record.id, "status": status, "score": score, "detail": detail}


def _execute_check(rule: QualityRule, table: MetaTable, ds: DataSource) -> tuple:
    cfg = rule.rule_config or {}
    if not ds or not table:
        return 0, {"error": "数据源或表不存在"}

    import pymysql
    catalog = (table.db_name or ds.database or "").strip()
    conn = pymysql.connect(host=ds.host, port=ds.port or 3306, user=ds.username, password=ds.password or "", database=catalog or ds.database or "")
    cursor = conn.cursor()
    tbl = table.table_name
    qtbl = f"`{catalog}`.`{tbl}`" if catalog else f"`{tbl}`"
    detail = {}

    if rule.rule_type == "completeness":
        col = cfg.get("column", "*")
        cursor.execute(f"SELECT COUNT(*) FROM {qtbl}")
        total = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM {qtbl} WHERE `{col}` IS NULL OR `{col}` = ''")
        null_count = cursor.fetchone()[0]
        score = int((1 - null_count / total) * 100) if total > 0 else 100
        detail = {"total": total, "null_count": null_count, "completeness": f"{score}%"}

    elif rule.rule_type == "uniqueness":
        col = cfg.get("column")
        cursor.execute(f"SELECT COUNT(*) FROM {qtbl}")
        total = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(DISTINCT `{col}`) FROM {qtbl}")
        unique = cursor.fetchone()[0]
        score = int(unique / total * 100) if total > 0 else 100
        detail = {"total": total, "unique": unique, "uniqueness": f"{score}%"}

    elif rule.rule_type == "accuracy":
        sql = cfg.get("sql")
        if sql:
            sql_run = sql.replace("{table}", qtbl).replace("{catalog}", catalog).replace("{bare_table}", f"`{tbl}`")
            cursor.execute(sql_run)
            result = cursor.fetchone()
            score = int(result[0]) if result else 0
            detail = {"result": result[0] if result else None}
        else:
            score, detail = 100, {}

    elif rule.rule_type in ("custom_sql", "dolphin_sql", "sql_rule"):
        # 自定义 SQL / 兼容 Dolphin 侧 SQL 规则：占位符 {table} 已带库限定
        sql_tpl = (cfg.get("sql") or "").strip()
        if not sql_tpl:
            conn.close()
            return 0, {"error": "请在 rule_config 中配置 sql"}
        sql_run = sql_tpl.replace("{table}", qtbl).replace("{catalog}", catalog or "").replace("{bare_table}", f"`{tbl}`")
        cursor.execute(sql_run)
        result = cursor.fetchone()
        if result is None or result[0] is None:
            score, detail = 0, {"message": "查询无结果"}
        else:
            try:
                score = int(float(result[0]))
            except (TypeError, ValueError):
                score = 100 if result[0] else 0
            detail = {"metric": result[0], "sql": sql_run[:500]}

    elif rule.rule_type == "timeliness":
        col = cfg.get("time_column")
        max_delay_hours = cfg.get("max_delay_hours", 24)
        cursor.execute(f"SELECT MAX(`{col}`) FROM {qtbl}")
        latest = cursor.fetchone()[0]
        from datetime import datetime
        if latest:
            delay = (datetime.utcnow() - latest).total_seconds() / 3600
            score = 100 if delay <= max_delay_hours else max(0, int(100 - (delay - max_delay_hours) * 5))
            detail = {"latest_time": str(latest), "delay_hours": round(delay, 2)}
        else:
            score, detail = 0, {"error": "无数据"}

    else:
        score, detail = 100, {"message": "规则类型暂不支持自动检查"}

    conn.close()
    return score, detail


def _eval_threshold(score: int, threshold: str) -> str:
    try:
        if eval(f"{score}{threshold}"):
            return "pass"
        return "fail"
    except Exception:
        return "warning"


@router.get("/rules/{rule_id}/records")
def list_check_records(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_quality_rule(db, current_user, rule_id)
    records = db.query(QualityCheckRecord).filter(QualityCheckRecord.rule_id == rule_id).order_by(QualityCheckRecord.id.desc()).limit(30).all()
    return records


@router.get("/dashboard")
def quality_dashboard(workspace_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    rules = db.query(QualityRule).filter(QualityRule.workspace_id == workspace_id).all()
    total_rules = len(rules)
    pass_count = fail_count = warning_count = 0
    for rule in rules:
        latest = db.query(QualityCheckRecord).filter(QualityCheckRecord.rule_id == rule.id).order_by(QualityCheckRecord.id.desc()).first()
        if latest:
            if latest.status == "pass":
                pass_count += 1
            elif latest.status == "fail":
                fail_count += 1
            else:
                warning_count += 1
    return {
        "total_rules": total_rules,
        "pass": pass_count,
        "fail": fail_count,
        "warning": warning_count,
        "pass_rate": f"{int(pass_count / total_rules * 100)}%" if total_rules > 0 else "N/A"
    }


@router.get("/rules/{rule_id}/trend")
def get_quality_trend(rule_id: int, days: int = 30, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """质量得分趋势（最近N天）"""
    require_quality_rule(db, current_user, rule_id)
    from datetime import timedelta
    since = datetime.utcnow() - timedelta(days=days)
    records = db.query(QualityCheckRecord).filter(
        QualityCheckRecord.rule_id == rule_id,
        QualityCheckRecord.checked_at >= since
    ).order_by(QualityCheckRecord.checked_at).all()
    return {
        "rule_id": rule_id,
        "trend": [
            {"date": r.checked_at.strftime("%Y-%m-%d %H:%M"), "score": r.score, "status": r.status}
            for r in records
        ]
    }


@router.get("/workspace-trend")
def get_workspace_quality_trend(workspace_id: int, days: int = 7, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """工作空间整体质量趋势"""
    assert_workspace_data_capability(db, current_user, workspace_id, "developer", PC.GIDO_BATCH_QUALITY_READ)
    from datetime import timedelta
    result = []
    for i in range(days - 1, -1, -1):
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
            QualityCheckRecord.checked_at <= day_end
        ).all()
        pass_c = sum(1 for r in records if r.status == "pass")
        fail_c = sum(1 for r in records if r.status == "fail")
        avg = int(sum(r.score or 0 for r in records) / len(records)) if records else 0
        result.append({"date": str(day), "pass": pass_c, "fail": fail_c, "avg_score": avg})
    return result

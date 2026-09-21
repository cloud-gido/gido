# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""数据质量检查执行：分区范围、失败抽样、强弱阻断、表级批量。"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.workspace import DataSource, MetaTable, QualityCheckRecord, QualityRule

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_THRESHOLD_RE = re.compile(r"^\s*(>=|<=|==|=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")
_SAMPLE_CAP = 20

SUPPORTED_RULE_TYPES = frozenset({
    "completeness",
    "uniqueness",
    "accuracy",
    "timeliness",
    "custom_sql",
    "dolphin_sql",
    "sql_rule",
    "validity",
    "consistency",
})

SEVERITY_WARN = "warn"
SEVERITY_BLOCK = "block"


def require_ident(name: str, *, label: str = "标识符") -> str:
    text_name = (name or "").strip()
    if not _IDENT_RE.match(text_name):
        raise HTTPException(status_code=400, detail=f"{label}不合法")
    return text_name


def normalize_severity(raw: Optional[str]) -> str:
    value = (raw or SEVERITY_WARN).strip().lower()
    if value in ("block", "strong", "hard"):
        return SEVERITY_BLOCK
    return SEVERITY_WARN


def validate_rule_type(rule_type: str) -> str:
    rt = (rule_type or "").strip()
    if rt not in SUPPORTED_RULE_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的规则类型: {rt or '空'}")
    return rt


def eval_threshold(score: float, threshold: Optional[str]) -> str:
    expr = (threshold or ">=0").strip()
    matched = _THRESHOLD_RE.match(expr)
    if not matched:
        return "warning"
    op, raw_rhs = matched.group(1), float(matched.group(2))
    left = float(score)
    if op == ">=":
        ok = left >= raw_rhs
    elif op == "<=":
        ok = left <= raw_rhs
    elif op in ("=", "=="):
        ok = left == raw_rhs
    elif op == ">":
        ok = left > raw_rhs
    else:
        ok = left < raw_rhs
    return "pass" if ok else "fail"


def resolve_bizdate(raw: Optional[str] = None) -> str:
    if raw and str(raw).strip():
        return str(raw).strip()[:32]
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def expand_bizdate(text: str, bizdate: str) -> str:
    value = str(text)
    value = value.replace("{bizdate}", bizdate)
    try:
        base = datetime.strptime(bizdate[:10], "%Y-%m-%d")
        value = value.replace("{bizdate-1}", (base - timedelta(days=1)).strftime("%Y-%m-%d"))
        value = value.replace("{today}", datetime.now().strftime("%Y-%m-%d"))
    except ValueError:
        pass
    return value


def partition_clause(cfg: Dict[str, Any], bizdate: str) -> Tuple[str, Tuple[Any, ...], Optional[str]]:
    """返回 (SQL 片段不含 WHERE, params, 说明)。只允许列=值，避免注入。"""
    col = (cfg.get("partition_column") or "").strip()
    if not col:
        return "", (), None
    col = require_ident(col, label="分区字段")
    if cfg.get("partition_value") is None or str(cfg.get("partition_value")).strip() == "":
        raise HTTPException(status_code=400, detail="已选分区字段时必须填写分区值，可用 {bizdate}")
    raw_val = expand_bizdate(str(cfg.get("partition_value")), bizdate)
    if len(raw_val) > 128:
        raise HTTPException(status_code=400, detail="分区值过长")
    return f"`{col}` = %s", (raw_val,), f"{col}={raw_val}"


def _where_prefix(partition_sql: str) -> str:
    if not partition_sql:
        return ""
    return f" WHERE ({partition_sql})"


def _fetch_sample(cursor, qtbl: str, where: str, params: Tuple[Any, ...], columns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    try:
        select_list = ", ".join(f"`{require_ident(c)}`" for c in columns) if columns else "*"
        cursor.execute(
            f"SELECT {select_list} FROM {qtbl} WHERE {where} LIMIT {_SAMPLE_CAP}",
            params,
        )
        desc = [d[0] for d in (cursor.description or [])]
        rows = []
        for row in cursor.fetchall() or []:
            item = {}
            for i, key in enumerate(desc):
                val = row[i]
                if hasattr(val, "isoformat"):
                    val = val.isoformat(sep=" ", timespec="seconds")
                item[str(key)] = val if val is None or isinstance(val, (int, float, str, bool)) else str(val)[:200]
            rows.append(item)
        return rows
    except Exception as e:
        logger.debug("quality sample skipped: %s", e)
        return []


def execute_rule_sql(
    rule: QualityRule,
    table: Optional[MetaTable],
    ds: Optional[DataSource],
    *,
    bizdate: Optional[str] = None,
) -> Tuple[int, Dict[str, Any]]:
    cfg = dict(rule.rule_config or {})
    if not ds or not table:
        return 0, {"error": "数据源或表不存在"}

    import pymysql

    biz = resolve_bizdate(bizdate)
    catalog = (table.db_name or ds.database or "").strip()
    if catalog:
        catalog = require_ident(catalog, label="库名")
    tbl = require_ident(table.table_name, label="表名")
    part_sql, part_params, part_label = partition_clause(cfg, biz)
    where_prefix = _where_prefix(part_sql)
    scope_params = part_params

    conn = pymysql.connect(
        host=ds.host,
        port=ds.port or 3306,
        user=ds.username,
        password=ds.password or "",
        database=catalog or ds.database or "",
    )
    cursor = conn.cursor()
    qtbl = f"`{catalog}`.`{tbl}`" if catalog else f"`{tbl}`"
    detail: Dict[str, Any] = {"bizdate": biz}
    if part_label:
        detail["partition"] = part_label

    try:
        if rule.rule_type == "completeness":
            col = require_ident(str(cfg.get("column") or ""), label="字段名")
            cursor.execute(f"SELECT COUNT(*) FROM {qtbl}{where_prefix}", scope_params)
            total = int(cursor.fetchone()[0] or 0)
            bad = f"`{col}` IS NULL OR `{col}` = ''"
            bad_where = f"({bad})" + (f" AND ({part_sql})" if part_sql else "")
            cursor.execute(f"SELECT COUNT(*) FROM {qtbl} WHERE {bad_where}", scope_params)
            null_count = int(cursor.fetchone()[0] or 0)
            score = int((1 - null_count / total) * 100) if total > 0 else 100
            detail.update({"total": total, "null_count": null_count, "completeness": f"{score}%"})
            if null_count:
                detail["sample_rows"] = _fetch_sample(cursor, qtbl, bad_where, scope_params, [col])

        elif rule.rule_type == "uniqueness":
            col = require_ident(str(cfg.get("column") or ""), label="字段名")
            cursor.execute(f"SELECT COUNT(*) FROM {qtbl}{where_prefix}", scope_params)
            total = int(cursor.fetchone()[0] or 0)
            cursor.execute(f"SELECT COUNT(DISTINCT `{col}`) FROM {qtbl}{where_prefix}", scope_params)
            unique = int(cursor.fetchone()[0] or 0)
            score = int(unique / total * 100) if total > 0 else 100
            detail.update({"total": total, "unique": unique, "uniqueness": f"{score}%"})
            if total > unique:
                try:
                    group_where = f"({part_sql})" if part_sql else "1=1"
                    cursor.execute(
                        f"SELECT `{col}` AS value, COUNT(*) AS cnt FROM {qtbl} "
                        f"WHERE {group_where} GROUP BY `{col}` HAVING COUNT(*) > 1 "
                        f"ORDER BY cnt DESC LIMIT {_SAMPLE_CAP}",
                        scope_params,
                    )
                    detail["sample_rows"] = [
                        {"value": r[0], "count": r[1]} for r in (cursor.fetchall() or [])
                    ]
                except Exception as e:
                    logger.debug("uniqueness sample skipped: %s", e)

        elif rule.rule_type == "accuracy":
            sql = (cfg.get("sql") or "").strip()
            if not sql:
                return 0, {"error": "准确性规则需要配置 sql，返回一个数值得分"}
            score, metric = _run_metric_sql(cursor, sql, qtbl, catalog, tbl, biz)
            detail.update(metric)

        elif rule.rule_type in ("custom_sql", "dolphin_sql", "sql_rule"):
            sql_tpl = (cfg.get("sql") or "").strip()
            if not sql_tpl:
                return 0, {"error": "请在规则参数中配置 sql"}
            score, metric = _run_metric_sql(cursor, sql_tpl, qtbl, catalog, tbl, biz)
            detail.update(metric)

        elif rule.rule_type == "timeliness":
            col = require_ident(str(cfg.get("time_column") or ""), label="时间字段")
            max_delay_hours = float(cfg.get("max_delay_hours") or 24)
            cursor.execute(f"SELECT MAX(`{col}`) FROM {qtbl}{where_prefix}", scope_params)
            latest = cursor.fetchone()[0]
            if latest:
                latest_dt = latest if hasattr(latest, "replace") else datetime.fromisoformat(
                    str(latest).replace("Z", "+00:00").split("+")[0]
                )
                delay = (datetime.utcnow() - latest_dt.replace(tzinfo=None)).total_seconds() / 3600
                score = 100 if delay <= max_delay_hours else max(0, int(100 - (delay - max_delay_hours) * 5))
                detail.update({
                    "latest_time": str(latest),
                    "delay_hours": round(delay, 2),
                    "max_delay_hours": max_delay_hours,
                })
            else:
                score, detail = 0, {**detail, "error": "无数据"}

        elif rule.rule_type == "validity":
            score, vdetail = _execute_validity(cursor, qtbl, cfg, part_sql, scope_params)
            detail.update(vdetail)

        elif rule.rule_type == "consistency":
            score, cdetail = _execute_consistency(cursor, qtbl, catalog, tbl, cfg, biz)
            detail.update(cdetail)

        else:
            return 0, {"error": f"规则类型暂不支持自动检查: {rule.rule_type}"}
    finally:
        conn.close()

    return int(score), detail


def _run_metric_sql(cursor, sql: str, qtbl: str, catalog: str, tbl: str, bizdate: str) -> Tuple[int, Dict[str, Any]]:
    sql_run = expand_bizdate(sql, bizdate)
    sql_run = (
        sql_run.replace("{table}", qtbl)
        .replace("{catalog}", catalog or "")
        .replace("{bare_table}", f"`{tbl}`")
    )
    cursor.execute(sql_run)
    result = cursor.fetchone()
    if result is None or result[0] is None:
        return 0, {"message": "查询无结果", "sql": sql_run[:500]}
    try:
        score = int(float(result[0]))
    except (TypeError, ValueError):
        score = 100 if result[0] else 0
    return score, {"metric": result[0], "sql": sql_run[:500]}


def _execute_validity(
    cursor,
    qtbl: str,
    cfg: Dict[str, Any],
    part_sql: str,
    scope_params: Tuple[Any, ...],
) -> Tuple[int, Dict[str, Any]]:
    col = require_ident(str(cfg.get("column") or ""), label="字段名")
    where_prefix = f" WHERE ({part_sql})" if part_sql else ""
    cursor.execute(f"SELECT COUNT(*) FROM {qtbl}{where_prefix}", scope_params)
    total = int(cursor.fetchone()[0] or 0)
    if total <= 0:
        return 100, {"total": 0, "invalid": 0, "validity": "100%"}

    mode = (cfg.get("mode") or "").strip().lower()
    part_and = f" AND ({part_sql})" if part_sql else ""

    if mode == "regex" or cfg.get("pattern"):
        pattern = str(cfg.get("pattern") or "")
        if not pattern or len(pattern) > 256:
            raise HTTPException(status_code=400, detail="有效性正则不能为空且不超过 256 字符")
        bad = f"`{col}` IS NULL OR `{col}` = '' OR `{col}` NOT REGEXP %s"
        params = scope_params + (pattern,)
        cursor.execute(f"SELECT COUNT(*) FROM {qtbl} WHERE ({bad}){part_and}", params)
        invalid = int(cursor.fetchone()[0] or 0)
        score = int((1 - invalid / total) * 100)
        detail = {"total": total, "invalid": invalid, "mode": "regex", "validity": f"{score}%"}
        if invalid:
            detail["sample_rows"] = _fetch_sample(cursor, qtbl, f"({bad}){part_and}", params, [col])
        return score, detail

    if mode == "enum" or cfg.get("allowed_values") is not None:
        values = cfg.get("allowed_values") or []
        if not isinstance(values, list) or not values:
            raise HTTPException(status_code=400, detail="有效性枚举至少需要一个允许值")
        if len(values) > 50:
            raise HTTPException(status_code=400, detail="有效性枚举最多 50 个值")
        cleaned = [str(v)[:128] for v in values]
        placeholders = ", ".join(["%s"] * len(cleaned))
        bad = f"`{col}` IS NULL OR `{col}` = '' OR `{col}` NOT IN ({placeholders})"
        params = scope_params + tuple(cleaned)
        cursor.execute(f"SELECT COUNT(*) FROM {qtbl} WHERE ({bad}){part_and}", params)
        invalid = int(cursor.fetchone()[0] or 0)
        score = int((1 - invalid / total) * 100)
        detail = {"total": total, "invalid": invalid, "mode": "enum", "validity": f"{score}%"}
        if invalid:
            detail["sample_rows"] = _fetch_sample(cursor, qtbl, f"({bad}){part_and}", params, [col])
        return score, detail

    if mode == "range" or cfg.get("min_value") is not None or cfg.get("max_value") is not None:
        clauses = [f"`{col}` IS NULL"]
        params_list: List[Any] = list(scope_params)
        if cfg.get("min_value") is not None:
            clauses.append(f"`{col}` < %s")
            params_list.append(cfg.get("min_value"))
        if cfg.get("max_value") is not None:
            clauses.append(f"`{col}` > %s")
            params_list.append(cfg.get("max_value"))
        bad = " OR ".join(clauses)
        params = tuple(params_list)
        cursor.execute(f"SELECT COUNT(*) FROM {qtbl} WHERE ({bad}){part_and}", params)
        invalid = int(cursor.fetchone()[0] or 0)
        score = int((1 - invalid / total) * 100)
        detail = {"total": total, "invalid": invalid, "mode": "range", "validity": f"{score}%"}
        if invalid:
            detail["sample_rows"] = _fetch_sample(cursor, qtbl, f"({bad}){part_and}", params, [col])
        return score, detail

    raise HTTPException(
        status_code=400,
        detail="有效性规则需要配置 mode=regex|enum|range，或提供 pattern / allowed_values / min_value|max_value",
    )


def _execute_consistency(
    cursor,
    qtbl: str,
    catalog: str,
    tbl: str,
    cfg: Dict[str, Any],
    bizdate: str,
) -> Tuple[int, Dict[str, Any]]:
    """左右两侧 SQL 各返回一个数值；相对误差在容差内则通过。"""
    left_sql = (cfg.get("left_sql") or cfg.get("sql_a") or "").strip()
    right_sql = (cfg.get("right_sql") or cfg.get("sql_b") or "").strip()
    if not left_sql or not right_sql:
        raise HTTPException(
            status_code=400,
            detail="一致性规则需要 left_sql 与 right_sql，各返回一个可比较的数值",
        )
    left_score, left_detail = _run_metric_sql(cursor, left_sql, qtbl, catalog, tbl, bizdate)
    right_score, right_detail = _run_metric_sql(cursor, right_sql, qtbl, catalog, tbl, bizdate)
    left_v = float(left_detail.get("metric") if left_detail.get("metric") is not None else left_score)
    right_v = float(right_detail.get("metric") if right_detail.get("metric") is not None else right_score)
    tolerance = float(cfg.get("tolerance_pct") if cfg.get("tolerance_pct") is not None else 0)
    denom = max(abs(left_v), abs(right_v), 1.0)
    diff_pct = abs(left_v - right_v) / denom * 100.0
    if diff_pct <= tolerance:
        score = 100
    else:
        score = max(0, int(100 - (diff_pct - tolerance)))
    return score, {
        "left": left_v,
        "right": right_v,
        "diff_pct": round(diff_pct, 4),
        "tolerance_pct": tolerance,
        "left_sql": left_detail.get("sql"),
        "right_sql": right_detail.get("sql"),
    }


def run_rule_check(
    db: Session,
    rule: QualityRule,
    *,
    bizdate: Optional[str] = None,
    notify: bool = True,
    trigger: str = "manual",
) -> Dict[str, Any]:
    """执行一条规则：写记录、告警、返回 should_block。供 API 与定时任务共用。"""
    from app.services.alert import alert_quality_failed
    from app.services.alert_center import open_quality_alert, resolve_quality_alerts

    if not rule.is_active:
        raise HTTPException(status_code=400, detail="规则已停用，启用后再执行检查")

    table = db.query(MetaTable).filter(MetaTable.id == rule.table_id).first()
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first() if table else None
    severity = normalize_severity(getattr(rule, "severity", None))
    biz = resolve_bizdate(bizdate)

    score, detail, status = 0, {"trigger": trigger, "bizdate": biz}, "fail"
    try:
        score, detail = execute_rule_sql(rule, table, ds, bizdate=biz)
        detail = {**detail, "trigger": trigger}
        status = eval_threshold(score, rule.threshold or ">=0")
    except HTTPException:
        raise
    except Exception as e:
        detail = {"error": str(e)[:500], "trigger": trigger, "bizdate": biz}
        status = "fail"

    record = QualityCheckRecord(rule_id=rule.id, status=status, score=int(score), detail=detail)
    db.add(record)
    db.flush()

    blocking = severity == SEVERITY_BLOCK and status == "fail"
    if status == "fail":
        open_quality_alert(
            db,
            workspace_id=rule.workspace_id,
            rule=rule,
            score=int(score),
            threshold=rule.threshold or ">=0",
            blocking=blocking,
            notify=notify,
        )
        if notify:
            try:
                alert_quality_failed(rule.rule_name, int(score), rule.threshold or ">=0")
            except Exception:
                pass
    elif status == "pass":
        resolve_quality_alerts(db, rule_id=rule.id)

    db.commit()
    db.refresh(record)
    return {
        "record_id": record.id,
        "rule_id": rule.id,
        "rule_name": rule.rule_name,
        "status": status,
        "score": int(score),
        "detail": detail,
        "severity": severity,
        "blocking": blocking,
        "should_block": blocking,
        "bizdate": biz,
    }


def run_table_checks(
    db: Session,
    table_id: int,
    *,
    bizdate: Optional[str] = None,
    notify: bool = True,
    trigger: str = "table",
) -> Dict[str, Any]:
    """跑一张表上全部启用规则。任一强规则失败则 should_block=true。"""
    rules = (
        db.query(QualityRule)
        .filter(QualityRule.table_id == table_id, QualityRule.is_active.is_(True))
        .order_by(QualityRule.id.asc())
        .all()
    )
    results = []
    should_block = False
    fail_count = 0
    for rule in rules:
        try:
            one = run_rule_check(db, rule, bizdate=bizdate, notify=notify, trigger=trigger)
        except HTTPException as e:
            one = {
                "rule_id": rule.id,
                "rule_name": rule.rule_name,
                "status": "fail",
                "score": 0,
                "detail": {"error": e.detail},
                "severity": normalize_severity(getattr(rule, "severity", None)),
                "blocking": normalize_severity(getattr(rule, "severity", None)) == SEVERITY_BLOCK,
                "should_block": normalize_severity(getattr(rule, "severity", None)) == SEVERITY_BLOCK,
            }
        results.append(one)
        if one.get("status") == "fail":
            fail_count += 1
        if one.get("should_block"):
            should_block = True
    return {
        "table_id": table_id,
        "bizdate": resolve_bizdate(bizdate),
        "total": len(results),
        "fail": fail_count,
        "pass": sum(1 for r in results if r.get("status") == "pass"),
        "should_block": should_block,
        "blocking": should_block,
        "results": results,
    }

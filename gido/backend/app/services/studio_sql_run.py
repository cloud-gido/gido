# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据开发 SQL 节点执行：按数据源类型连接（PostgreSQL / MySQL / Doris）。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import DataSource, TaskNode, Workspace
from app.services.integration_runtime import normalize_ds_type, open_connection
from app.services.sql_readonly import (
    apply_readonly_row_limit,
    column_types_from_description,
    json_cell_value,
    split_sql_statements,
)
from app.services.workspace_datasource_policy import load_datasource_for_run


def resolve_sql_datasource(db: Session, node: TaskNode) -> DataSource:
    """已配置 datasource_id 的节点用原配置；未配置则继承工作空间默认。"""
    ds = load_datasource_for_run(
        db,
        workspace_id=node.workspace_id,
        explicit_datasource_id=node.datasource_id,
        role="SQL 节点数据源",
    )
    return ds


def _adapt_statement(stmt: str, ds_type: str) -> str:
    """将常见 MySQL/Doris 探查语句转为 PostgreSQL 等价 SQL。"""
    s = stmt.strip().rstrip(";").strip()
    lt = (ds_type or "").lower()
    if lt != "postgresql":
        return s
    if re.match(r"^show\s+tables\b", s, re.IGNORECASE):
        return (
            "SELECT table_schema AS schema, table_name AS name "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "AND table_type = 'BASE TABLE' "
            "ORDER BY 1, 2"
        )
    if re.match(r"^show\s+databases\b", s, re.IGNORECASE):
        return "SELECT datname AS database FROM pg_database WHERE datistemplate = false ORDER BY 1"
    if re.match(r"^desc(?:ribe)?\s+(\S+)", s, re.IGNORECASE):
        m = re.match(r"^desc(?:ribe)?\s+(\S+)", s, re.IGNORECASE)
        tbl = (m.group(1) if m else "").strip("`\"")
        return (
            "SELECT column_name, data_type, is_nullable, column_default "
            f"FROM information_schema.columns WHERE table_name = '{tbl}' "
            "ORDER BY ordinal_position"
        )
    return s


def _looks_like_result_query(stmt: str) -> bool:
    """判断是否可能返回结果集，以便追加 LIMIT / fetchmany 封顶。"""
    core = (stmt or "").strip().lstrip("(").strip()
    return bool(re.match(r"(?is)^(with|select|show|desc|describe|explain)\b", core))


def run_sql_with_result(
    node: TaskNode,
    db: Session,
    bizdate: Optional[str] = None,
    *,
    resolve_date_expr,
) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """
    执行节点脚本，返回 (log_lines, result_meta)。
    resolve_date_expr: studio._resolve_date_expr 注入，避免循环导入。
    """
    ds = resolve_sql_datasource(db, node)
    lt = normalize_ds_type(ds)

    ws = db.query(Workspace).filter(Workspace.id == node.workspace_id).first()
    tz_name = (ws.timezone if ws and ws.timezone else None) or settings.DEFAULT_TIMEZONE

    try:
        import pytz

        now_local = datetime.now(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.now()

    source = "节点配置" if node.datasource_id else "工作空间默认"
    logs: List[str] = [
        f"[INFO] 数据源({source}): {ds.name} ({ds.ds_type}) #{ds.id} @ {ds.host}:{ds.port or (5432 if lt == 'postgresql' else 3306)}",
    ]

    from app.services.workspace_variables import substitute_script_variables

    from app.services.business_date import bizdate_and_yesterday, normalize_business_date

    biz, yesterday_str = bizdate_and_yesterday(
        normalize_business_date(bizdate),
        now=now_local.replace(tzinfo=None),
    )
    script = substitute_script_variables(
        db, int(node.workspace_id), node.script_content or "", "batch", bizdate=bizdate
    )
    script = script.replace("${bizdate}", biz).replace("${yesterday}", yesterday_str)

    from app.services.date_macros import expand_date_macros_in_text

    macro_biz = normalize_business_date(bizdate)
    if node.params and isinstance(node.params, dict):
        for k, v in node.params.items():
            val = expand_date_macros_in_text(str(v), bizdate=macro_biz, tz_name=tz_name)
            script = script.replace(f"${{{k}}}", val)

    script = expand_date_macros_in_text(script, bizdate=macro_biz, tz_name=tz_name)

    raw_parts = split_sql_statements(script, max_parts=64)
    if not raw_parts:
        raise ValueError("SQL 脚本为空")

    result_data: Optional[Dict[str, Any]] = None
    last_select_result: Optional[Dict[str, Any]] = None
    _cap = 10000

    try:
        with open_connection(ds) as opened:
            kind = opened[0]
            conn = opened[1]
            cur = conn.cursor()
            try:
                for raw_stmt in raw_parts:
                    stmt = _adapt_statement(raw_stmt, lt)
                    if stmt != raw_stmt.strip().rstrip(";").strip():
                        logs.append(f"[INFO] 已转换为 PostgreSQL 语法: {stmt[:120]}...")
                    exec_stmt = stmt
                    if _looks_like_result_query(stmt):
                        capped = apply_readonly_row_limit(stmt, _cap)
                        if capped != stmt:
                            logs.append(f"[INFO] 已追加 LIMIT {_cap} 避免全表拉取")
                            exec_stmt = capped
                    logs.append(f"[SQL] {exec_stmt[:200]}")
                    cur.execute(exec_stmt)
                    if cur.description:
                        # fetchmany 封顶，避免已有大 LIMIT 时仍把结果全量载入内存
                        rows = cur.fetchmany(_cap + 1)
                        truncated = len(rows) > _cap
                        rows = rows[:_cap]
                        columns = [d[0] for d in cur.description]
                        col_types = column_types_from_description(ds.ds_type, cur.description)
                        last_select_result = {
                            "columns": columns,
                            "column_types": col_types,
                            "rows": [[json_cell_value(v) for v in row] for row in rows],
                            "total": len(rows),
                            "truncated": truncated,
                        }
                        logs.append(
                            f"[INFO] 返回 {len(rows)} 行"
                            + ("（已截断）" if truncated else "")
                        )
                    else:
                        if kind == "mysql":
                            conn.commit()
                        logs.append(f"[INFO] 影响行数: {getattr(cur, 'rowcount', 0)}")
            finally:
                cur.close()
    except Exception as e:
        err = str(e).strip()
        if lt == "postgresql" and "pymysql" in err.lower():
            err = f"{err}（请确认节点已绑定 postgresql 数据源，而非 doris/mysql）"
        raise RuntimeError(f"连接或执行失败 [{ds.name} / {ds.ds_type}]: {err}") from e

    result_data = last_select_result
    return logs, result_data

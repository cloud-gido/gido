# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据开发 SQL 节点执行：按数据源类型连接（PostgreSQL / MySQL / Doris）。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import DataSource, TaskNode, Workspace
from app.services.adhoc_sql_driver import (
    ExecutionBinding,
    is_server_timeout_error,
    registered_connection,
    statement_handle,
    tagged_sql,
)
from app.services.integration_runtime import normalize_ds_type, open_connection
from app.services.sql_readonly import (
    _strip_sql_comments,
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
    """是否可在语句末尾安全追加 LIMIT（仅 SELECT / WITH…SELECT）。

    SHOW / DESC / EXPLAIN / USE 等常不支持 LIMIT（如 Doris ``SHOW ROUTINE LOAD``），
    行数仍由下方 ``fetchmany`` 封顶，不必改写 SQL。
    """
    core = _strip_sql_comments((stmt or "").strip().lstrip("(")).strip()
    return bool(re.match(r"(?is)^(with|select)\b", core))


def run_sql_with_result(
    node: TaskNode,
    db: Session,
    bizdate: Optional[str] = None,
    *,
    resolve_date_expr,
    emit: Optional[Callable[[str], None]] = None,
    control: Optional[Callable[[], None]] = None,
    execution_binding: Optional[ExecutionBinding] = None,
    timeout_seconds: Optional[int] = None,
    run_id: Optional[int] = None,
    lease_token: Optional[str] = None,
    statement_callbacks: Optional[Dict[str, Callable[..., Any]]] = None,
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
    if emit:
        emit(logs[0])

    def add_log(line: str) -> None:
        logs.append(line)
        if emit:
            emit(line)

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

    callbacks = dict(statement_callbacks or {})
    durable_callbacks = run_id is not None and bool(lease_token)
    if run_id is not None or lease_token is not None:
        if run_id is None or not lease_token:
            raise ValueError("run_id 与 lease_token 必须同时提供")
        from app.services import adhoc_run_store as run_store

        callbacks = {
            "initialize": lambda statements: run_store.initialize_run_statements(
                db, run_id, statements, lease_token
            ),
            "start": lambda index: run_store.start_run_statement(
                db, run_id, index, lease_token
            ),
            "chunk": lambda index, rows, **meta: run_store.append_statement_result_chunk(
                db,
                run_id,
                index,
                rows,
                lease_token,
                max_rows=max(1, int(settings.ADHOC_RESULT_MAX_ROWS)),
                max_bytes=max(1, int(settings.ADHOC_RESULT_MAX_BYTES)),
                **meta,
            ),
            "success": lambda index, **meta: run_store.complete_run_statement_success(
                db, run_id, index, lease_token, **meta
            ),
            "failed": lambda index, error: run_store.complete_run_statement_failed(
                db, run_id, index, lease_token, str(error)
            ),
            "skipped": lambda index, reason: run_store.complete_run_statement_skipped(
                db, run_id, index, lease_token, str(reason)
            ),
        }
    if callbacks.get("initialize"):
        callbacks["initialize"](raw_parts)

    result_data: Optional[Dict[str, Any]] = None
    last_select_result: Optional[Dict[str, Any]] = None
    statement_results: List[Dict[str, Any]] = []
    _cap = max(1, int(settings.ADHOC_RESULT_MAX_ROWS))
    chunk_rows = max(1, int(settings.ADHOC_RESULT_CHUNK_ROWS))
    effective_timeout = max(
        1, int(timeout_seconds or getattr(node, "timeout_seconds", None) or 300)
    )
    effective_binding = execution_binding or (
        ExecutionBinding(run_id, lease_token, 0)
        if run_id is not None and lease_token
        else None
    )

    try:
        connection_context = (
            registered_connection(
                ds,
                effective_binding,
                timeout_seconds=effective_timeout,
            )
            if effective_binding
            else open_connection(ds)
        )
        with connection_context as opened:
            kind = opened[0]
            conn = opened[1]
            cur = conn.cursor()
            try:
                for statement_index, raw_stmt in enumerate(raw_parts):
                    try:
                        if control:
                            control()
                        if callbacks.get("start"):
                            callbacks["start"](statement_index)
                        stmt = _adapt_statement(raw_stmt, lt)
                        if stmt != raw_stmt.strip().rstrip(";").strip():
                            add_log(f"[INFO] 已转换为 PostgreSQL 语法: {stmt[:120]}...")
                        exec_stmt = stmt
                        if _looks_like_result_query(stmt):
                            capped = apply_readonly_row_limit(stmt, _cap)
                            if capped != stmt:
                                add_log(f"[INFO] 已追加 LIMIT {_cap} 避免全表拉取")
                                exec_stmt = capped
                        add_log(f"[SQL] {exec_stmt[:200]}")
                        if effective_binding:
                            statement_binding = ExecutionBinding(
                                effective_binding.run_id,
                                effective_binding.lease_token,
                                statement_index,
                            )
                            exec_stmt = tagged_sql(exec_stmt, statement_binding, lt)
                            with statement_handle(conn, lt, statement_binding):
                                cur.execute(exec_stmt)
                        else:
                            cur.execute(exec_stmt)
                        if control:
                            control()
                        if cur.description:
                            columns = [d[0] for d in cur.description]
                            col_types = column_types_from_description(ds.ds_type, cur.description)
                            rows: List[List[Any]] = []
                            truncated = False
                            while len(rows) < _cap:
                                if control:
                                    control()
                                fetched = cur.fetchmany(
                                    min(chunk_rows, _cap - len(rows)) + (1 if len(rows) + chunk_rows >= _cap else 0)
                                )
                                if not fetched:
                                    break
                                accepted = fetched[: _cap - len(rows)]
                                converted = [
                                    [json_cell_value(value) for value in item]
                                    for item in accepted
                                ]
                                if converted and callbacks.get("chunk"):
                                    persisted = callbacks["chunk"](
                                        statement_index,
                                        converted,
                                        columns=columns,
                                        column_types=col_types,
                                        truncated=len(fetched) > len(accepted),
                                    )
                                    if durable_callbacks and (
                                        persisted is None
                                        or int(getattr(persisted, "row_count", 0))
                                        < len(converted)
                                    ):
                                        truncated = True
                                rows.extend(converted)
                                if truncated and durable_callbacks:
                                    break
                                if len(fetched) > len(accepted):
                                    truncated = True
                                    break
                                if len(fetched) < min(chunk_rows, _cap - len(rows) + len(fetched)):
                                    break
                            if len(rows) >= _cap and not truncated:
                                overflow = cur.fetchmany(1)
                                truncated = bool(overflow)
                            last_select_result = {
                                "index": statement_index,
                                "sql": raw_stmt.strip(),
                                "columns": columns,
                                "column_types": col_types,
                                "rows": rows,
                                "total": len(rows),
                                "truncated": truncated,
                            }
                            statement_results.append(last_select_result)
                            if kind == "postgresql":
                                conn.commit()
                            if callbacks.get("success"):
                                callbacks["success"](
                                    statement_index,
                                    columns=columns,
                                    column_types=col_types,
                                    truncated=truncated,
                                )
                            add_log(
                                f"[INFO] 返回 {len(rows)} 行"
                                + ("（已截断）" if truncated else "")
                            )
                        else:
                            affected = int(getattr(cur, "rowcount", 0) or 0)
                            if kind in ("mysql", "postgresql"):
                                conn.commit()
                            if callbacks.get("success"):
                                callbacks["success"](
                                    statement_index, affected_rows=affected
                                )
                            add_log(f"[INFO] 影响行数: {affected}")
                    except Exception as statement_error:
                        if kind == "postgresql":
                            conn.rollback()
                        if callbacks.get("failed"):
                            callbacks["failed"](statement_index, statement_error)
                        reason = f"前序语句 {statement_index + 1} 执行失败"
                        for skipped_index in range(statement_index + 1, len(raw_parts)):
                            if callbacks.get("skipped"):
                                callbacks["skipped"](skipped_index, reason)
                        raise
            finally:
                cur.close()
    except Exception as e:
        if isinstance(e, (InterruptedError, TimeoutError)):
            raise
        if effective_binding and is_server_timeout_error(e):
            raise TimeoutError(
                f"运行超过 {effective_timeout} 秒"
            ) from e
        err = str(e).strip()
        if lt == "postgresql" and "pymysql" in err.lower():
            err = f"{err}（请确认节点已绑定 postgresql 数据源，而非 doris/mysql）"
        raise RuntimeError(f"连接或执行失败 [{ds.name} / {ds.ds_type}]: {err}") from e

    if last_select_result is not None:
        # 顶层保留最后一个结果集，兼容旧前端/同步 API；statements 供多结果页签使用。
        result_data = {
            **last_select_result,
            "statement_count": len(raw_parts),
            "result_set_count": len(statement_results),
            "statements": statement_results,
        }
    return logs, result_data

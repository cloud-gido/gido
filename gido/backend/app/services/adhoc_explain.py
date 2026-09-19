# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Explicit, read-only query plan capture for materialized ad-hoc statements."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.workspace import AdhocRunStatement, DataSource
from app.services.adhoc_sql_driver import connection_query_id
from app.services.integration_runtime import normalize_ds_type, open_connection
from app.services.sql_readonly import (
    assert_readonly_statement,
    column_fields_from_description,
    column_types_from_description,
    field_packets_from_cursor,
    json_cell_value,
    split_sql_statements,
)

EXPLAIN_TIMEOUT_SECONDS = 10


def capture_statement_plan(
    db: Session,
    *,
    datasource: DataSource,
    statement: AdhocRunStatement,
) -> Dict[str, Any]:
    parts = split_sql_statements(statement.sql_text or "", max_parts=2)
    if len(parts) != 1:
        raise ValueError("Explain 仅支持单条只读语句")
    sql = assert_readonly_statement(parts[0])
    ds_type = normalize_ds_type(datasource)
    started = datetime.utcnow()
    with open_connection(datasource) as opened:
        conn = opened[1]
        cur = conn.cursor()
        try:
            if ds_type == "postgresql":
                cur.execute("SET statement_timeout = %s", (EXPLAIN_TIMEOUT_SECONDS * 1000,))
            else:
                try:
                    cur.execute(
                        f"SET SESSION MAX_EXECUTION_TIME = {EXPLAIN_TIMEOUT_SECONDS * 1000}"
                    )
                except Exception:
                    pass
                if ds_type == "doris":
                    cur.execute(f"SET query_timeout = {EXPLAIN_TIMEOUT_SECONDS}")
            cur.execute(f"EXPLAIN {sql}")
            description = cur.description or []
            raw_rows = list(cur.fetchall() or [])
            columns = [str(item[0]) for item in description]
            packets = field_packets_from_cursor(cur)
            column_types = column_types_from_description(ds_type, description, packets)
            fields = column_fields_from_description(ds_type, description, packets)
            rows = [[json_cell_value(value) for value in row] for row in raw_rows]
            query_id = connection_query_id(conn, ds_type)
        finally:
            cur.close()
    finished = datetime.utcnow()
    snapshot: Dict[str, Any] = {
        "kind": "explain",
        "analyze": False,
        "datasource_type": ds_type,
        "columns": columns,
        "column_types": column_types,
        "fields": fields,
        "rows": rows,
        "query_id": query_id,
        "timeout_seconds": EXPLAIN_TIMEOUT_SECONDS,
        "captured_at": finished.isoformat(),
        "duration_ms": max(0, int((finished - started).total_seconds() * 1000)),
    }
    statement.plan_snapshot = snapshot
    db.commit()
    db.refresh(statement)
    return snapshot

# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""本地文件导入：DDL、流式装数、Doris Stream Load、可选注册数据字典。"""
from __future__ import annotations

import csv
import logging
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.workspace import DataSource, MetaTable
from app.services.datasource_mysql_user import mysql_protocol_connect_user
from app.services.file_import_parse import (
    LOGICAL_TYPES,
    coerce_cell,
    iter_csv_rows_from_path,
    iter_xlsx_rows_from_path,
)
from app.services.file_import_store import load_meta, resolve_data_path
from app.services.integration_runtime import assert_supported_ds, open_connection, quote_ident

logger = logging.getLogger(__name__)

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_table_name(name: str) -> str:
    t = (name or "").strip()
    if not t or len(t) > 64:
        raise ValueError("目标表名须为 1–64 字符")
    if not _TABLE_NAME_RE.match(t):
        raise ValueError("目标表名仅允许字母、数字、下划线，且不能以数字开头")
    return t


def normalize_columns(columns: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not columns:
        raise ValueError("至少需要一列")
    used: set[str] = set()
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(columns):
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {i + 1} 列缺少列名")
        if not re.match(r"^[\w\u4e00-\u9fff]+$", name) or name[0].isdigit():
            raise ValueError(f"非法列名: {name}")
        key = name.lower()
        if key in used:
            raise ValueError(f"列名重复: {name}")
        used.add(key)
        lt = str(raw.get("type") or "string").lower()
        if lt not in LOGICAL_TYPES:
            raise ValueError(f"不支持的字段类型: {lt}")
        out.append(
            {
                "name": name,
                "type": lt,
                "nullable": bool(raw.get("nullable", True)),
                "is_primary_key": bool(raw.get("is_primary_key", False)),
            }
        )
    return out


def _sql_type(ds_type: str, logical: str) -> str:
    lt = (ds_type or "").lower()
    if logical == "bigint":
        return "BIGINT"
    if logical == "double":
        return "DOUBLE"
    if logical == "boolean":
        return "TINYINT(1)"
    if logical == "datetime":
        return "DATETIME"
    if lt == "doris":
        return "VARCHAR(65533)"
    return "VARCHAR(1024)"


def build_create_table_ddl(
    ds: DataSource,
    table_name: str,
    columns: Sequence[Dict[str, Any]],
) -> str:
    lt = assert_supported_ds(ds, "目标")
    if lt == "postgresql":
        raise ValueError("本地文件导入暂仅支持 MySQL / Doris 目标")
    table_name = validate_table_name(table_name)
    cols = normalize_columns(columns)
    lines: List[str] = []
    pk_cols = [c["name"] for c in cols if c.get("is_primary_key")]
    for c in cols:
        null_sql = "" if c.get("nullable", True) else " NOT NULL"
        lines.append(f"  {quote_ident(lt, c['name'])} {_sql_type(lt, c['type'])}{null_sql}")

    if lt == "doris":
        key_cols = pk_cols or [cols[0]["name"]]
        key_sql = ", ".join(quote_ident(lt, c) for c in key_cols)
        dist = quote_ident(lt, key_cols[0])
        body = ",\n".join(lines)
        return (
            f"CREATE TABLE IF NOT EXISTS {quote_ident(lt, table_name)} (\n{body}\n) "
            f"ENGINE=OLAP\nDUPLICATE KEY({key_sql})\n"
            f"DISTRIBUTED BY HASH({dist}) BUCKETS 8\n"
            f'PROPERTIES ("replication_num" = "1")'
        )

    if pk_cols:
        lines.append(f"  PRIMARY KEY ({', '.join(quote_ident(lt, c) for c in pk_cols)})")
    body = ",\n".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {quote_ident(lt, table_name)} (\n{body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"


def table_exists(ds: DataSource, table_name: str) -> bool:
    table_name = validate_table_name(table_name)
    lt = assert_supported_ds(ds, "目标")
    if lt == "postgresql":
        raise ValueError("本地文件导入暂仅支持 MySQL / Doris 目标")
    with open_connection(ds) as opened:
        _, conn = opened
        cur = conn.cursor()
        schema = (ds.database or "").strip()
        cur.execute(
            "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s LIMIT 1",
            (schema, table_name),
        )
        return cur.fetchone() is not None


def _register_datamap(
    db: Session,
    *,
    workspace_id: int,
    ds: DataSource,
    table_name: str,
    owner: Optional[str] = None,
) -> Optional[int]:
    existing = (
        db.query(MetaTable)
        .filter(
            MetaTable.workspace_id == workspace_id,
            MetaTable.datasource_id == ds.id,
            MetaTable.table_name == table_name,
        )
        .first()
    )
    if existing:
        return existing.id
    table = MetaTable(
        workspace_id=workspace_id,
        datasource_id=ds.id,
        db_name=(ds.database or "").strip() or None,
        table_name=table_name,
        table_comment="本地文件导入",
        table_type="table",
        owner=owner,
    )
    db.add(table)
    db.commit()
    db.refresh(table)
    try:
        from app.api.datamap import _sync_table_schema

        _sync_table_schema(db, table, ds)
    except Exception:
        logger.exception("file import datamap schema sync failed for %s", table_name)
    return table.id


def doris_http_port(ds: DataSource) -> int:
    ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
    for key in ("http_port", "fe_http_port", "doris_http_port"):
        if ex.get(key) is not None and str(ex.get(key)).strip() != "":
            return int(ex[key])
    mysql_port = int(ds.port or 9030)
    if mysql_port == 9030:
        return int(settings.FILE_IMPORT_DORIS_HTTP_PORT or 8030)
    # 常见部署：HTTP = MySQL - 1000
    if mysql_port > 1000:
        return mysql_port - 1000
    return int(settings.FILE_IMPORT_DORIS_HTTP_PORT or 8030)


def _ensure_table(ds: DataSource, table_name: str, ddl: str, exists: bool) -> None:
    if exists:
        return
    with open_connection(ds) as opened:
        _, conn = opened
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()


def _load_mysql_batched(
    ds: DataSource,
    table_name: str,
    cols: List[Dict[str, Any]],
    row_iter,
    *,
    batch_size: int,
    max_rows: int,
) -> Tuple[int, int]:
    lt = "mysql"
    col_list = ", ".join(quote_ident(lt, c["name"]) for c in cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO {quote_ident(lt, table_name)} ({col_list}) VALUES ({placeholders})"
    width = len(cols)
    rows_read = 0
    rows_written = 0
    sample_errors: List[str] = []
    batch: List[Tuple[Any, ...]] = []

    with open_connection(ds) as opened:
        _, conn = opened
        cur = conn.cursor()
        for row in row_iter:
            rows_read += 1
            if rows_read > max_rows:
                raise ValueError(f"行数超过上限 {max_rows}")
            try:
                values: List[Any] = []
                for i, c in enumerate(cols):
                    raw_cell = row[i] if i < len(row) else None
                    values.append(coerce_cell(raw_cell, c["type"]))
                # 补齐/截断到列数
                if len(values) < width:
                    values.extend([None] * (width - len(values)))
                batch.append(tuple(values[:width]))
            except Exception as e:
                if len(sample_errors) < 5:
                    sample_errors.append(f"第 {rows_read} 行: {e}")
                continue

            if len(batch) >= batch_size:
                cur.executemany(insert_sql, batch)
                conn.commit()
                rows_written += len(batch)
                batch = []

        if batch:
            cur.executemany(insert_sql, batch)
            conn.commit()
            rows_written += len(batch)

    if sample_errors and rows_written == 0:
        raise ValueError("装数失败: " + "; ".join(sample_errors))
    if sample_errors:
        logger.warning("file import coerce errors table=%s samples=%s", table_name, sample_errors)
    return rows_read, rows_written


# Doris Stream Load CSV：NULL 必须写成 \N；空字段 "" 对 BIGINT/DATETIME 会整行被过滤
_DORIS_CSV_NULL = "\\N"


def _cell_for_doris_csv(value: Any) -> Any:
    return _DORIS_CSV_NULL if value is None else value


def _rows_to_csv_temp(row_iter, cols: List[Dict[str, Any]]) -> Tuple[Path, int]:
    """把数据行流式写成 UTF-8 CSV（无表头），供 Doris Stream Load。返回 (csv_path, rows)。"""
    tmp = tempfile.NamedTemporaryFile(prefix="gido_import_", suffix=".csv", delete=False)
    tmp_path = Path(tmp.name)
    rows = 0
    skipped = 0
    width = len(cols)
    sample_errors: List[str] = []
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            for row in row_iter:
                try:
                    cells = []
                    for i, c in enumerate(cols):
                        raw_cell = row[i] if i < len(row) else None
                        v = coerce_cell(raw_cell, c["type"])
                        cells.append(_cell_for_doris_csv(v))
                    if len(cells) < width:
                        cells.extend([_DORIS_CSV_NULL] * (width - len(cells)))
                    writer.writerow(cells[:width])
                    rows += 1
                except Exception as e:
                    skipped += 1
                    if len(sample_errors) < 5:
                        sample_errors.append(f"第 {rows + skipped} 行: {e}")
                    continue
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        tmp.close()
    if rows == 0:
        detail = "; ".join(sample_errors) if sample_errors else "文件无有效数据行"
        raise ValueError(f"装数失败: {detail}")
    if sample_errors:
        logger.warning(
            "file import coerce skipped rows=%s samples=%s", skipped, sample_errors
        )
    return tmp_path, rows


def _xlsx_to_csv_temp(
    path: Path,
    *,
    has_header: bool,
    sheet_name: Optional[str],
    cols: List[Dict[str, Any]],
    max_rows: int,
) -> Tuple[Path, int]:
    """把 Excel 流式写成 UTF-8 CSV，供 Doris Stream Load。返回 (csv_path, rows)。"""
    return _rows_to_csv_temp(
        iter_xlsx_rows_from_path(
            path, has_header=has_header, sheet_name=sheet_name, max_rows=max_rows
        ),
        cols,
    )


def _csv_to_stream_load_temp(
    path: Path,
    *,
    encoding: Optional[str],
    delimiter: Optional[str],
    has_header: bool,
    cols: List[Dict[str, Any]],
    max_rows: int,
) -> Tuple[Path, int]:
    """
    CSV → 无表头 UTF-8 CSV（并按列类型 coerce）。
    Doris skip_header 是整数行数，传 true 会被忽略，表头当数据导致 DATA_QUALITY_ERROR。
    """
    delim = delimiter if delimiter is not None and delimiter != "" else ","
    return _rows_to_csv_temp(
        iter_csv_rows_from_path(
            path,
            encoding=encoding,
            delimiter=delim,
            has_header=has_header,
            max_rows=max_rows,
        ),
        cols,
    )


def _fetch_doris_error_sample(error_url: str, auth: Tuple[str, str], *, limit: int = 3) -> str:
    """拉取 Stream Load ErrorURL 前几行，便于定位类型/列数问题。"""
    if not error_url:
        return ""
    try:
        resp = requests.get(error_url, auth=auth, timeout=15)
        text = (resp.text or "").strip()
        if not text:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:limit]
        return " | ".join(lines)[:800]
    except Exception as e:
        logger.info("fetch doris ErrorURL failed: %s", e)
        return ""


def _doris_stream_load_put(
    url: str,
    *,
    headers: Dict[str, str],
    auth: Tuple[str, str],
    csv_path: Path,
) -> requests.Response:
    """
    Doris FE Stream Load 常 307 到 BE。requests 自动跟跳时会丢掉 Authorization，
    于是 BE 返回 [NOT_AUTHORIZED]no valid Basic authorization。
    这里手动跟随一次重定向并重新带上 Basic Auth。
    """
    with csv_path.open("rb") as f:
        resp = requests.put(
            url,
            data=f,
            headers=headers,
            auth=auth,
            timeout=3600,
            allow_redirects=False,
        )
    if resp.status_code not in (301, 302, 303, 307, 308):
        return resp
    location = (resp.headers.get("Location") or resp.headers.get("location") or "").strip()
    if not location:
        return resp
    logger.info("doris stream load redirect %s -> %s", resp.status_code, location)
    with csv_path.open("rb") as f:
        return requests.put(
            location,
            data=f,
            headers=headers,
            auth=auth,
            timeout=3600,
            allow_redirects=False,
        )


def _doris_stream_load(
    ds: DataSource,
    table_name: str,
    cols: List[Dict[str, Any]],
    csv_path: Path,
    *,
    column_separator: str = ",",
    skip_header: bool = False,
    charset: str = "UTF-8",
) -> Tuple[int, int]:
    db_name = (ds.database or "").strip()
    if not db_name:
        raise ValueError("Doris 数据源未配置 database")
    host = (ds.host or "").strip() or "127.0.0.1"
    http_port = doris_http_port(ds)
    user = mysql_protocol_connect_user(ds)
    password = ds.password or ""
    label = f"gido_fi_{uuid.uuid4().hex[:16]}"
    url = f"http://{host}:{http_port}/api/{db_name}/{table_name}/_stream_load"
    col_names = ",".join(c["name"] for c in cols)
    headers = {
        "Expect": "100-continue",
        "label": label,
        "format": "csv",
        "column_separator": column_separator,
        "columns": col_names,
        "max_filter_ratio": "0.1",
        "strict_mode": "false",
        "timeout": "3600",
    }
    # Doris skip_header 是「跳过行数」(整数)，不是布尔；调用方应先去掉表头再传 False
    if skip_header:
        headers["skip_header"] = "1"

    enc = (charset or "UTF-8").upper()
    if enc not in ("UTF-8", "UTF8"):
        headers["charset"] = enc

    size = csv_path.stat().st_size
    logger.info(
        "doris stream load start url=%s size=%s label=%s user=%s",
        url,
        size,
        label,
        user,
    )
    resp = _doris_stream_load_put(
        url,
        headers=headers,
        auth=(user, password),
        csv_path=csv_path,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"Status": "Fail", "Message": resp.text[:2000]}

    status = str(body.get("Status") or body.get("status") or "").strip().lower()
    loaded = int(body.get("NumberLoadedRows") or body.get("numberLoadedRows") or 0)
    read = int(body.get("NumberTotalRows") or body.get("numberTotalRows") or loaded)
    filtered = int(body.get("NumberFilteredRows") or body.get("numberFilteredRows") or 0)
    error_url = str(
        body.get("ErrorURL") or body.get("ErrorUrl") or body.get("errorURL") or ""
    ).strip()
    ok = status in ("success", "publish timeout") or (resp.status_code < 400 and loaded > 0)
    if not ok:
        msg = str(body.get("Message") or body.get("msg") or body)
        detail_parts = [f"Doris Stream Load 失败 HTTP {resp.status_code}: {msg}"]
        if read or filtered:
            detail_parts.append(f"filtered={filtered}/{read or '?'}")
        if error_url:
            detail_parts.append(f"ErrorURL={error_url}")
            sample = _fetch_doris_error_sample(error_url, (user, password))
            if sample:
                detail_parts.append(f"样例={sample}")
        hint = ""
        if "NOT_AUTHORIZED" in msg.upper() or "no valid Basic authorization" in msg:
            hint = (
                "（Stream Load 鉴权失败：请在数据源填写 Doris 用户名/密码；"
                "空账号会按 root 空密码尝试。若仍失败，核对 FE HTTP 端口与账号权限。）"
            )
        elif "too many filtered" in msg.lower() or "DATA_QUALITY_ERROR" in msg.upper():
            hint = (
                "（大量行被过滤：常见原因是空值未写成 \\N、表头被当数据、分隔符不匹配或类型转换失败。"
                "请核对列类型；错误样例见上方。）"
            )
        raise ValueError("; ".join(detail_parts) + hint)
    if filtered and loaded == 0:
        sample = _fetch_doris_error_sample(error_url, (user, password)) if error_url else ""
        raise ValueError(
            f"Doris Stream Load 全部被过滤: filtered={filtered}/{read}"
            + (f"; ErrorURL={error_url}" if error_url else f"; body={body}")
            + (f"; 样例={sample}" if sample else "")
        )
    logger.info("doris stream load done loaded=%s read=%s filtered=%s", loaded, read, filtered)
    return read or loaded, loaded


def execute_file_import(
    db: Session,
    *,
    workspace_id: int,
    ds: DataSource,
    table_name: str,
    file_id: str,
    columns: Sequence[Dict[str, Any]],
    encoding: Optional[str] = None,
    delimiter: Optional[str] = None,
    has_header: bool = True,
    sheet_name: Optional[str] = None,
    register_datamap: bool = False,
    owner: Optional[str] = None,
    batch_size: Optional[int] = None,
    if_exists: str = "fail",
) -> Tuple[int, int, str]:
    """建表（若不存在）+ 流式装数。Doris CSV 走 Stream Load。"""
    lt = assert_supported_ds(ds, "目标")
    if lt not in ("mysql", "doris"):
        raise ValueError("本地文件导入暂仅支持 MySQL / Doris 目标")
    table_name = validate_table_name(table_name)
    cols = normalize_columns(columns)
    ddl = build_create_table_ddl(ds, table_name, cols)
    mode = (if_exists or "fail").strip().lower()
    if mode not in ("fail", "append"):
        raise ValueError("if_exists 仅支持 fail / append")

    exists = table_exists(ds, table_name)
    if exists and mode == "fail":
        raise ValueError(f"目标表已存在: {table_name}（请更换表名，或选择追加写入）")

    meta = load_meta(workspace_id, file_id)
    path = resolve_data_path(meta)
    fmt = str(meta.get("format") or "csv").lower()
    max_rows = int(settings.FILE_IMPORT_MAX_ROWS or 5_000_000)
    batch = int(batch_size or settings.FILE_IMPORT_MYSQL_BATCH or 5000)
    batch = max(500, min(batch, 20000))

    _ensure_table(ds, table_name, ddl, exists)

    cleanup_paths: List[Path] = []
    try:
        if lt == "doris":
            # 统一：先写成无表头 UTF-8 CSV（含类型 coerce），再 Stream Load。
            # 避免 Doris skip_header 语义差异（须传整数行数，不能传 true）。
            if fmt == "csv":
                tmp_csv, converted = _csv_to_stream_load_temp(
                    path,
                    encoding=encoding,
                    delimiter=delimiter,
                    has_header=has_header,
                    cols=cols,
                    max_rows=max_rows,
                )
            else:
                tmp_csv, converted = _xlsx_to_csv_temp(
                    path,
                    has_header=has_header,
                    sheet_name=sheet_name,
                    cols=cols,
                    max_rows=max_rows,
                )
            cleanup_paths.append(tmp_csv)
            rows_read, rows_written = _doris_stream_load(
                ds,
                table_name,
                cols,
                tmp_csv,
                column_separator=",",
                skip_header=False,
            )
            if converted and rows_read == 0:
                rows_read = converted
        else:
            # MySQL：流式批量 INSERT
            if fmt == "csv":
                delim = delimiter if delimiter is not None and delimiter != "" else ","
                row_iter = iter_csv_rows_from_path(
                    path,
                    encoding=encoding,
                    delimiter=delim,
                    has_header=has_header,
                    max_rows=max_rows,
                )
            else:
                row_iter = iter_xlsx_rows_from_path(
                    path,
                    has_header=has_header,
                    sheet_name=sheet_name,
                    max_rows=max_rows,
                )
            rows_read, rows_written = _load_mysql_batched(
                ds, table_name, cols, row_iter, batch_size=batch, max_rows=max_rows
            )
    finally:
        for p in cleanup_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    if register_datamap:
        _register_datamap(
            db,
            workspace_id=workspace_id,
            ds=ds,
            table_name=table_name,
            owner=owner,
        )

    return rows_read, rows_written, ddl

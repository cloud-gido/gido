# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""数据服务：SQL 绑定、只读执行、脱敏、分页、简易缓存与限流。"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import secrets
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import verify_password, get_password_hash
from app.models.data_service import ConsumerApp, DataApi, DataApiParam
from app.models.workspace import DataSource
from app.services.data_api_response import (  # noqa: F401 — re-export for callers
    build_list_page_data,
    new_trace_id,
    open_error_envelope,
    open_success_envelope,
    pop_pagination_params,
    rows_to_object_list,
    wrap_count_sql,
)
from app.services.datasource_mysql_user import mysql_protocol_connect_user
from app.services.shared_state import (
    cache_get as shared_cache_get,
    cache_set as shared_cache_set,
    rate_limit_hit,
)
from app.services.sql_readonly import assert_readonly_statement, result_set_from_cursor

_PARAM_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")

_cache_lock = threading.Lock()
_result_cache: Dict[str, Tuple[float, dict]] = {}
_rate_lock = threading.Lock()
_rate_buckets: Dict[str, List[float]] = {}


def generate_app_credentials() -> Tuple[str, str, str]:
    """返回 (app_key, app_secret_plain, app_secret_hash)。"""
    key = secrets.token_hex(8)
    secret = secrets.token_urlsafe(24)
    return key, secret, get_password_hash(secret)


def verify_app_secret(app: ConsumerApp, secret: str) -> bool:
    return verify_password(secret, app.app_secret_hash)


def check_ip_whitelist(client_ip: str, whitelist: Optional[List[str]]) -> bool:
    if not whitelist:
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for item in whitelist:
        s = (item or "").strip()
        if not s:
            continue
        try:
            if "/" in s:
                if addr in ipaddress.ip_network(s, strict=False):
                    return True
            elif addr == ipaddress.ip_address(s):
                return True
        except ValueError:
            continue
    return False


def check_rate_limit(bucket_key: str, limit: int, window_sec: int = 60) -> None:
    if limit <= 0:
        return
    try:
        exceeded = rate_limit_hit(f"data-api:{bucket_key}", limit, window_sec)
    except RuntimeError as ex:
        raise HTTPException(status_code=503, detail=str(ex)) from ex
    if exceeded is not None:
        if exceeded:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后重试")
        return
    now = time.time()
    with _rate_lock:
        hits = _rate_buckets.get(bucket_key, [])
        hits = [t for t in hits if now - t < window_sec]
        if len(hits) >= limit:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后重试")
        hits.append(now)
        _rate_buckets[bucket_key] = hits


def wizard_to_sql(wizard: dict, params: List[DataApiParam]) -> str:
    """向导配置 → SELECT 模板（参数占位 :name）。

    支持 ``order_by: [{column, direction}]``。未配置时：若返回字段为具体列，
    默认按前两列 ASC，避免开放分页结果顺序不稳定（与 SQL 模式 ORDER BY 对齐）。
    """
    if not wizard:
        raise HTTPException(status_code=400, detail="向导配置为空")
    table = (wizard.get("table") or "").strip()
    if not table or not re.match(r"^[a-zA-Z0-9_.`]+$", table):
        raise HTTPException(status_code=400, detail="无效的表名")
    fields = wizard.get("fields") or ["*"]
    if fields == ["*"] or fields == "*":
        select_cols = "*"
        concrete_fields: List[str] = []
    else:
        safe = []
        for f in fields:
            col = str(f).strip()
            if not re.match(r"^[a-zA-Z0-9_.`]+$", col):
                raise HTTPException(status_code=400, detail=f"无效字段名: {col}")
            if col != "*":
                safe.append(col)
        if not safe:
            select_cols = "*"
            concrete_fields = []
        else:
            select_cols = ", ".join(safe)
            concrete_fields = safe
    where_parts = ["1=1"]
    filters = wizard.get("filters") or []
    for flt in filters:
        col = (flt.get("column") or "").strip()
        op = (flt.get("op") or "=").strip().upper()
        param = (flt.get("param") or "").strip()
        if not col or not param:
            continue
        if not re.match(r"^[a-zA-Z0-9_.`]+$", col):
            raise HTTPException(status_code=400, detail=f"无效过滤列: {col}")
        if op not in ("=", "!=", ">", ">=", "<", "<=", "LIKE"):
            raise HTTPException(status_code=400, detail=f"不支持的比较符: {op}")
        where_parts.append(f"({col} {op} :{param} OR :{param} IS NULL)")
    for p in params:
        if p.name not in [f.get("param") for f in filters]:
            continue
    sql = f"SELECT {select_cols} FROM {table} WHERE {' AND '.join(where_parts)}"
    order_parts = _wizard_order_by_clauses(wizard.get("order_by"), concrete_fields)
    if order_parts:
        sql = f"{sql} ORDER BY {', '.join(order_parts)}"
    return sql


def _wizard_order_by_clauses(order_by: Any, concrete_fields: List[str]) -> List[str]:
    """解析向导排序；空配置时用返回字段前两列 ASC 保底。"""
    items: List[Any]
    if isinstance(order_by, list) and order_by:
        items = order_by
    elif concrete_fields:
        items = [{"column": c, "direction": "ASC"} for c in concrete_fields[:2]]
    else:
        return []
    parts: List[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            col = item.strip()
            direction = "ASC"
        elif isinstance(item, dict):
            col = str(item.get("column") or "").strip()
            direction = str(item.get("direction") or "ASC").strip().upper()
        else:
            continue
        if not col or not re.match(r"^[a-zA-Z0-9_.`]+$", col):
            raise HTTPException(status_code=400, detail=f"无效排序列: {col or '?'}")
        if direction not in ("ASC", "DESC"):
            raise HTTPException(status_code=400, detail=f"无效排序方向: {direction}")
        key = col.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{col} {direction}")
    return parts


def _coerce_param_value(p: DataApiParam, raw: Any) -> Any:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if p.default_value is not None and str(p.default_value).strip() != "":
            raw = p.default_value
        elif p.required:
            raise HTTPException(status_code=400, detail=f"缺少必填参数: {p.name}")
        else:
            return None
    dt = (p.data_type or "string").lower()
    if dt == "string":
        val = str(raw)
    elif dt == "int":
        val = int(raw)
    elif dt == "long":
        val = int(raw)
    elif dt == "float":
        val = float(raw)
    elif dt == "bool":
        val = str(raw).lower() in ("1", "true", "yes", "on")
    elif dt == "date":
        val = str(raw)[:10]
        datetime.strptime(val, "%Y-%m-%d")
    elif dt == "datetime":
        val = str(raw)
        if "T" in val:
            datetime.fromisoformat(val.replace("Z", "+00:00")[:19])
        else:
            datetime.strptime(val[:19], "%Y-%m-%d %H:%M:%S")
    else:
        val = str(raw)
    if p.validator_regex:
        if not re.match(p.validator_regex, str(val)):
            raise HTTPException(status_code=400, detail=f"参数 {p.name} 格式不合法")
    return val


def bind_params(api: DataApi, raw_params: Dict[str, Any], sql: str = "") -> Dict[str, Any]:
    param_defs = {p.name: p for p in (api.params or [])}
    sql_names = set(_PARAM_RE.findall(sql or ""))
    bound: Dict[str, Any] = {}

    for name in sql_names:
        p = param_defs.get(name)
        if p:
            bound[name] = _coerce_param_value(p, raw_params.get(name))
            continue
        raw = raw_params.get(name)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            raise HTTPException(
                status_code=400,
                detail=f"SQL 使用了 :{name}，请先在「API 参数」中添加同名参数并保存，或在测试中传入该值",
            )
        bound[name] = raw if isinstance(raw, (int, float, bool)) else str(raw)

    for p in api.params or []:
        if p.name not in bound:
            bound[p.name] = _coerce_param_value(p, raw_params.get(p.name))

    allowed = set(param_defs.keys()) | sql_names | {
        "PageNumber",
        "pageNumber",
        "page",
        "page_no",
        "pageNo",
        "PageSize",
        "pageSize",
        "page_size",
    }
    unknown = set(raw_params.keys()) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知参数: {', '.join(sorted(unknown))}")
    return bound


def sql_template_to_driver(sql: str) -> str:
    """`:name` → `%(name)s`（PostgreSQL / psycopg2）。"""
    def repl(m):
        return f"%({m.group(1)})s"
    return _PARAM_RE.sub(repl, sql)


def compile_sql_literals(sql: str, bound: Dict[str, Any]) -> str:
    """`:name` → 已转义字面量（MySQL / Doris，避免服务端解析 `:param`）。"""
    import pymysql.converters as conv

    def repl(m):
        name = m.group(1)
        if name not in bound:
            raise HTTPException(status_code=400, detail=f"SQL 参数未定义: {name}")
        val = bound[name]
        if val is None:
            return "NULL"
        return conv.escape_item(val, "utf8")

    compiled = _PARAM_RE.sub(repl, sql.rstrip(";"))
    if _PARAM_RE.search(compiled):
        raise HTTPException(status_code=400, detail="SQL 仍含未替换的参数占位符")
    return compiled


def apply_pagination(
    sql: str,
    *,
    page_no: int,
    page_size: int,
    enabled: bool,
) -> Tuple[str, int, int]:
    """分页：默认在语句末尾追加 LIMIT/OFFSET，保留业务 SQL 的 ORDER BY。

    旧实现把 SQL 包进 ``SELECT * FROM (...) LIMIT`` 子查询。Doris/MySQL 优化器
    常会丢掉内层无 LIMIT 的 ORDER BY，导致下游「条件不变、顺序每次不同」。
    与 Studio 只读 ``apply_readonly_row_limit`` 对齐：能追加则不包一层。

    若模板自身已含 LIMIT，才回退到子查询包装（无法在外层安全叠加 OFFSET）。
    """
    page_no = max(1, page_no)
    page_size = max(1, min(page_size, 10000))
    if not enabled:
        return sql, page_no, page_size
    offset = (page_no - 1) * page_size
    core = (sql or "").strip().rstrip(";").strip()
    from app.services.sql_readonly import _strip_sql_comments

    cleaned = _strip_sql_comments(core)
    if re.search(r"\bLIMIT\s+\d", cleaned, re.IGNORECASE):
        wrapped = f"SELECT * FROM ({core}) AS _dw_api_sub LIMIT {page_size} OFFSET {offset}"
        return wrapped, page_no, page_size
    appended = f"{core} LIMIT {page_size} OFFSET {offset}"
    return appended, page_no, page_size


def _mask_value(val: Any, mask_type: Optional[str]) -> Any:
    if val is None or not mask_type:
        return val
    s = str(val)
    mt = mask_type.lower()
    if mt == "phone" and len(s) >= 7:
        return s[:3] + "****" + s[-4:]
    if mt == "id_card" and len(s) >= 8:
        return s[:4] + "**********" + s[-4:]
    if mt == "email" and "@" in s:
        local, domain = s.split("@", 1)
        return (local[:1] + "***@" + domain) if local else s
    if mt == "name" and s:
        return s[0] + "*" * max(0, len(s) - 1)
    if mt == "hash":
        return hashlib.sha256(s.encode()).hexdigest()[:16]
    return s


def apply_response_mask(rows: List[List[Any]], columns: List[str], schema: Optional[List[dict]]) -> List[List[Any]]:
    if not schema:
        return rows
    mask_map: Dict[str, str] = {}
    for item in schema:
        name = item.get("name") or item.get("alias")
        if name and item.get("mask_type"):
            mask_map[str(name)] = item["mask_type"]
    if not mask_map:
        return rows
    idx_map = {c: i for i, c in enumerate(columns)}
    out = []
    for row in rows:
        nr = list(row)
        for col, mi in idx_map.items():
            if col in mask_map:
                nr[mi] = _mask_value(nr[mi], mask_map[col])
        out.append(nr)
    return out


def _cache_get(key: str, ttl: int) -> Optional[dict]:
    if ttl <= 0:
        return None
    shared = shared_cache_get(f"data-api:{key}")
    if shared is not None:
        return shared
    with _cache_lock:
        item = _result_cache.get(key)
        if not item:
            return None
        exp, val = item
        if time.time() > exp:
            del _result_cache[key]
            return None
        return val


def _cache_set(key: str, val: dict, ttl: int) -> None:
    if ttl <= 0:
        return
    if shared_cache_set(f"data-api:{key}", val, ttl):
        return
    with _cache_lock:
        _result_cache[key] = (time.time() + ttl, val)


def execute_data_api(
    db: Session,
    api: DataApi,
    ds: DataSource,
    raw_params: Dict[str, Any],
    *,
    page_no: int = 1,
    page_size: Optional[int] = None,
    skip_cache: bool = False,
) -> dict:
    sql_tpl = (api.sql_template or "").strip()
    if api.mode == "wizard" and api.wizard_config:
        sql_tpl = wizard_to_sql(api.wizard_config, list(api.params or []))
    if not sql_tpl:
        raise HTTPException(status_code=400, detail="API 未配置 SQL")

    from app.services.workspace_variables import substitute_script_variables

    sql_tpl = substitute_script_variables(db, int(api.workspace_id), sql_tpl, "serve")

    assert_readonly_statement(re.sub(r":([a-zA-Z_][a-zA-Z0-9_]*)", "1", sql_tpl))

    bound = bind_params(api, raw_params, sql_tpl)
    ps = page_size or api.page_size_default or 20
    ps = min(ps, api.page_size_max or 1000)

    cache_key = None
    if api.cache_ttl_seconds and not skip_cache:
        cache_key = hashlib.sha256(
            f"{api.id}:{sql_tpl}:{bound}:{page_no}:{ps}".encode()
        ).hexdigest()
        cached = _cache_get(cache_key, api.cache_ttl_seconds)
        if cached:
            cached = dict(cached)
            cached["cache_hit"] = True
            return cached

    lt = (ds.ds_type or "").lower()
    if lt in ("mysql", "doris"):
        base_sql = compile_sql_literals(sql_tpl, bound)
    else:
        base_sql = sql_template_to_driver(sql_tpl)

    pagination_on = bool(api.pagination_enabled)
    exec_sql, pg_no, pg_sz = apply_pagination(
        base_sql,
        page_no=page_no,
        page_size=ps,
        enabled=pagination_on,
    )
    count_sql = wrap_count_sql(base_sql) if pagination_on else None

    timeout = max(3, min(api.timeout_seconds or 30, 120))
    max_rows = max(1, min(api.max_rows or 10000, 50000))
    total_count: Optional[int] = None

    if lt in ("mysql", "doris"):
        import pymysql

        port = ds.port or (9030 if lt == "doris" else 3306)
        conn = pymysql.connect(
            host=ds.host,
            port=port,
            user=mysql_protocol_connect_user(ds),
            password=ds.password or "",
            database=(ds.database or ""),
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
        )
        try:
            cur = conn.cursor()
            if count_sql:
                cur.execute(count_sql)
                crow = cur.fetchone()
                total_count = int(crow[0] if crow else 0)
            cur.execute(exec_sql)
            rows = cur.fetchall()
            base = result_set_from_cursor(lt, cur.description, rows, max_rows)
        except pymysql.Error as e:
            raise HTTPException(status_code=400, detail=f"数据源执行失败: {e}") from e
        finally:
            conn.close()
    elif lt == "postgresql":
        import psycopg2

        dbname = (ds.database or "").strip()
        if not dbname:
            raise HTTPException(status_code=400, detail="PostgreSQL 数据源未配置数据库名")
        conn = psycopg2.connect(
            host=ds.host or "127.0.0.1",
            port=ds.port or 5432,
            user=(ds.username or "").strip() or None,
            password=ds.password or "",
            dbname=dbname,
            connect_timeout=timeout,
        )
        try:
            cur = conn.cursor()
            if count_sql:
                cur.execute(count_sql, bound)
                crow = cur.fetchone()
                total_count = int(crow[0] if crow else 0)
            cur.execute(exec_sql, bound)
            rows = cur.fetchall()
            base = result_set_from_cursor(lt, cur.description, rows, max_rows)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"数据源执行失败: {e}") from e
        finally:
            conn.close()
    else:
        raise HTTPException(status_code=400, detail=f"数据服务暂不支持数据源类型: {ds.ds_type}")

    cols = base.get("columns") or []
    masked_rows = apply_response_mask(base.get("rows") or [], cols, api.response_fields)
    # 分页开启时 total 必须是全量匹配行数；关闭时沿用本页/截断后的行数
    total_for_page = total_count if total_count is not None else int(base.get("total") or 0)

    result = build_list_page_data(
        columns=cols,
        rows=masked_rows,
        total=total_for_page,
        page=pg_no,
        page_size=pg_sz,
        truncated=bool(base.get("truncated", False)),
        cache_hit=False,
    )
    if cache_key:
        # 缓存仅存对外 data 形态，不含内部列名标记
        _cache_set(cache_key, dict(result), api.cache_ttl_seconds)
    # 内部标记：调用方在返回开放信封前必须 pop，避免泄漏到对外 JSON
    result["__gido_columns__"] = list(cols)
    return result

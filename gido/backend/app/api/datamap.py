# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime
import re
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import MetaTable, MetaColumn, Lineage, DataSource, User, TaskNode
from app.services.rbac import assert_workspace_data_capability, require_meta_table, require_datasource_row, require_task_node
from app.services.datasource_mysql_user import mysql_protocol_connect_user

router = APIRouter(prefix="/datamap", tags=["数据地图"])

# 数据地图「刷新目录」：从这些类型的数据源拉物理表（与 JDBC 能力一致）
_CATALOG_DS_TYPES = frozenset({"mysql", "doris", "postgresql"})
_MYSQL_SYSTEM_SCHEMAS = frozenset({"information_schema", "mysql", "performance_schema", "sys", "__internal_schema"})
# 单数据源最多扫多少个库，避免共享集群扫爆；可用 extra_config.catalogs 精确指定
_DATAMAP_MAX_CATALOGS = 80


def _catalog_for_table(ds: Optional[DataSource], table: MetaTable) -> str:
    return (table.db_name or (ds.database if ds else "") or "").strip()


def _qualified_name(ds_name: str, catalog: str, table_name: str) -> str:
    if catalog:
        return f"{ds_name}.{catalog}.{table_name}"
    return f"{ds_name}.{table_name}"


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _require_ident(name: str, label: str) -> str:
    text = (name or "").strip()
    if not _IDENT_RE.match(text):
        raise ValueError(f"{label}含非法字符，无法生成语句")
    return text


def build_select_sql(ds_type: str, catalog: str, table_name: str, limit: int = 100) -> str:
    """只读探查语句。标识符白名单，避免把表名拼进可执行 SQL 时被注入。"""
    table = _require_ident(table_name, "表名")
    schema = _require_ident(catalog, "库名")
    capped = max(1, min(int(limit or 100), 1000))
    if (ds_type or "").lower() == "postgresql":
        return f'SELECT *\nFROM "{schema}"."{table}"\nLIMIT {capped}'
    return f"SELECT *\nFROM `{schema}`.`{table}`\nLIMIT {capped}"


def merge_column_hits(
    rows: List[dict],
    hits: List[dict],
    *,
    datasource_id: int,
    datasource_name: str,
    ds_type: str,
    meta_by_key: Dict,
) -> None:
    """字段名/注释命中时，给已有表打上 match_columns；表本身没命中则补一行。"""
    index: Dict[tuple, dict] = {}
    for row in rows:
        if row.get("datasource_id") == datasource_id and row.get("table_name"):
            index[(str(row.get("catalog") or ""), row["table_name"])] = row
    for hit in hits:
        catalog = str(hit.get("catalog") or "")
        table_name = str(hit.get("table_name") or "")
        column_name = str(hit.get("column_name") or "").strip()
        if not table_name or not column_name:
            continue
        comment = str(hit.get("column_comment") or "").strip()
        label = f"{column_name}（{comment}）" if comment else column_name
        existing = index.get((catalog, table_name))
        if existing is not None:
            matched = existing.setdefault("match_columns", [])
            if label not in matched and len(matched) < 3:
                matched.append(label)
            continue
        meta = meta_by_key.get((datasource_id, catalog, table_name))
        row = {
            "row_key": f"{'m' if meta else 'p'}-{datasource_id}-{catalog}-{table_name}",
            "registered": meta is not None,
            "meta_table_id": meta.id if meta else None,
            "datasource_id": datasource_id,
            "datasource_name": datasource_name,
            "ds_type": ds_type,
            "catalog": catalog,
            "table_name": table_name,
            "qualified_name": _qualified_name(datasource_name, catalog, table_name),
            "table_comment": (meta.table_comment if meta else "") or "",
            "table_type": (meta.table_type if meta else None) or "table",
            "row_count": meta.row_count if meta else None,
            "tags": meta.tags if meta else None,
            "owner": meta.owner if meta else None,
            "last_updated": meta.last_updated if meta else None,
            "match_columns": [label],
        }
        rows.append(row)
        index[(catalog, table_name)] = row


def _partition_summary(fetched) -> Optional[dict]:
    parts = [row for row in fetched if row and row[0]]
    if not parts:
        return None
    names = [str(row[0]) for row in parts]
    return {
        "method": parts[0][1] or None,
        "expression": parts[0][2] or None,
        "count": len(parts),
        "names": names[-8:],
    }


_SEARCH_HIT_CAP = 40


def _fetch_mysql_keyword_tables(cur, catalogs: List[str], keyword: str):
    """一次查出所有库里命中的表，避免按库逐条往返。"""
    like = f"%{keyword}%"
    marks = ",".join(["%s"] * len(catalogs))
    cur.execute(
        f"SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE, TABLE_COMMENT "
        f"FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA IN ({marks}) AND ("
        f"LOWER(TABLE_NAME) LIKE %s OR LOWER(IFNULL(TABLE_COMMENT,'')) LIKE %s "
        f"OR LOWER(TABLE_SCHEMA) LIKE %s"
        f") ORDER BY TABLE_SCHEMA, TABLE_NAME LIMIT %s",
        (*catalogs, like, like, like, _SEARCH_HIT_CAP),
    )
    return list(cur.fetchall())


def _explicit_datamap_catalogs(ds: DataSource) -> List[str]:
    """数据源 extra_config.catalogs / datamap_catalogs：逗号串或数组白名单。"""
    ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
    raw = ex.get("catalogs") if ex.get("catalogs") not in (None, "", []) else ex.get("datamap_catalogs")
    if isinstance(raw, str):
        return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _mysql_catalogs_to_scan(ds: DataSource, cur) -> List[str]:
    """
    数据地图枚举哪些库：
    1) 配置了 catalogs 白名单 → 只用白名单（共享集群推荐）
    2) 否则枚举账号可见的非系统库（默认库排前）
    3) information_schema.SCHEMATA 空时回退 SHOW DATABASES（部分 Doris 账号可见性差异）
    """
    explicit = _explicit_datamap_catalogs(ds)
    if explicit:
        return explicit[:_DATAMAP_MAX_CATALOGS]

    names: List[str] = []
    try:
        cur.execute(
            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME NOT IN ("
            + ",".join(["%s"] * len(_MYSQL_SYSTEM_SCHEMAS))
            + ") ORDER BY SCHEMA_NAME",
            tuple(sorted(_MYSQL_SYSTEM_SCHEMAS)),
        )
        names = [str(r[0]) for r in cur.fetchall() if r and r[0]]
    except Exception:
        names = []

    if not names:
        try:
            cur.execute("SHOW DATABASES")
            for r in cur.fetchall() or []:
                name = str(r[0]) if r else ""
                if name and name.lower() not in _MYSQL_SYSTEM_SCHEMAS:
                    names.append(name)
        except Exception:
            names = []

    default = (ds.database or "").strip()
    if default and default not in names:
        names.insert(0, default)
    elif default and default in names:
        names = [default] + [n for n in names if n != default]
    return names[:_DATAMAP_MAX_CATALOGS]


def _sync_table_schema(db: Session, table: MetaTable, ds: DataSource) -> int:
    """从物理库同步字段到 MetaColumn（含 COLUMN_COMMENT，与数据开发库表浏览器同源），返回字段数。

    行数只读 information_schema / reltuples 估算，禁止对业务表 SELECT COUNT(*)（打开路径不可阻塞大表）。
    """
    from app.services.integration_runtime import list_columns, open_connection, normalize_ds_type

    lt = normalize_ds_type(ds)
    if lt not in ("mysql", "doris", "postgresql"):
        raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型的结构同步: {ds.ds_type}")

    table_id = int(table.id)
    catalog = (table.db_name or ds.database or "").strip()
    if lt in ("mysql", "doris") and not catalog:
        raise HTTPException(
            status_code=400,
            detail="请在元数据或数据源上配置库名（catalog），以便定位物理表",
        )

    try:
        cols = list_columns(ds, table.table_name, catalog=catalog or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步字段失败: {e}") from e

    db.query(MetaColumn).filter(MetaColumn.table_id == table_id).delete()
    for i, col in enumerate(cols):
        comment = col.get("comment")
        if isinstance(comment, str):
            comment = comment.strip() or None
        key = str(col.get("key") or "").upper()
        db.add(
            MetaColumn(
                table_id=table_id,
                column_name=col.get("name") or "",
                column_type=col.get("type") or "",
                column_comment=comment,
                is_nullable=bool(col.get("nullable", True)),
                is_primary_key=(key == "PRI"),
                ordinal_position=i + 1,
            )
        )

    # 表注释 + 估算行数：同一 metadata 查询，最佳努力
    try:
        with open_connection(ds) as opened:
            if opened[0] == "mysql":
                _, conn = opened
                cur = conn.cursor()
                schema = catalog
                cur.execute(
                    "SELECT TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                    (schema, table.table_name),
                )
                tr = cur.fetchone()
                if tr:
                    if (tr[0] or "").strip():
                        table.table_comment = (tr[0] or "").strip()
                    if tr[1] is not None:
                        try:
                            table.row_count = int(tr[1])
                        except (TypeError, ValueError):
                            pass
            else:
                _, conn, _pg_default = opened
                cur = conn.cursor()
                ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
                schema = (table.db_name or ex.get("schema") or "public").strip() or "public"
                cur.execute(
                    """
                    SELECT obj_description(c.oid), c.reltuples::bigint
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND c.relname = %s
                    LIMIT 1
                    """,
                    (schema, table.table_name),
                )
                tr = cur.fetchone()
                if tr:
                    if (tr[0] or "").strip():
                        table.table_comment = (tr[0] or "").strip()
                    if tr[1] is not None:
                        try:
                            est = int(tr[1])
                            if est >= 0:
                                table.row_count = est
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass

    table.last_updated = datetime.utcnow()
    db.commit()
    return len(cols)


def _serialize_table_detail(db: Session, table: MetaTable) -> dict:
    columns = (
        db.query(MetaColumn)
        .filter(MetaColumn.table_id == table.id)
        .order_by(MetaColumn.ordinal_position)
        .all()
    )
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first()
    catalog = _catalog_for_table(ds, table)
    qual = _qualified_name(ds.name, catalog, table.table_name) if ds else None
    return {
        "id": table.id,
        "datasource_id": table.datasource_id,
        "datasource_name": ds.name if ds else None,
        "ds_type": ds.ds_type if ds else None,
        "catalog": catalog or None,
        "qualified_name": qual,
        "db_name": table.db_name,
        "table_name": table.table_name,
        "table_comment": table.table_comment,
        "table_type": table.table_type,
        "row_count": table.row_count,
        "size_bytes": table.size_bytes,
        "tags": table.tags,
        "owner": table.owner,
        "business_description": table.business_description,
        "last_updated": table.last_updated,
        "columns": [
            {
                "id": c.id,
                "name": c.column_name,
                "type": c.column_type,
                "comment": c.column_comment,
                "nullable": c.is_nullable,
                "primary_key": c.is_primary_key,
            }
            for c in columns
        ],
    }


class MetaTableCreate(BaseModel):
    workspace_id: int
    datasource_id: int
    db_name: Optional[str] = None
    table_name: str
    table_comment: Optional[str] = None
    table_type: str = "table"
    tags: Optional[List[str]] = None
    owner: Optional[str] = None


class MetaTableEnsure(BaseModel):
    """打开/展开目录表时幂等收录到数据字典。"""
    workspace_id: int
    datasource_id: int
    db_name: Optional[str] = None
    table_name: str
    table_comment: Optional[str] = None
    table_type: str = "table"
    sync_if_empty: bool = True


def _normalize_meta_db_name(ds: DataSource, db_name: Optional[str]) -> Optional[str]:
    s = (db_name or "").strip()
    if s:
        return s
    fallback = (ds.database or "").strip()
    return fallback or None


def _find_meta_table(
    db: Session,
    *,
    workspace_id: int,
    datasource_id: int,
    db_name: Optional[str],
    table_name: str,
) -> Optional[MetaTable]:
    want = (db_name or "").strip()
    rows = (
        db.query(MetaTable)
        .filter(
            MetaTable.workspace_id == workspace_id,
            MetaTable.datasource_id == datasource_id,
            MetaTable.table_name == table_name,
        )
        .all()
    )
    for t in rows:
        if (t.db_name or "").strip() == want:
            return t
    return None


def _ensure_meta_table(
    db: Session,
    *,
    workspace_id: int,
    datasource_id: int,
    table_name: str,
    db_name: Optional[str] = None,
    table_comment: Optional[str] = None,
    table_type: str = "table",
    tags: Optional[List[str]] = None,
    owner: Optional[str] = None,
    sync_if_empty: bool = True,
    force_sync: bool = False,
) -> tuple[MetaTable, DataSource, bool, int, Optional[str]]:
    """
    返回 (table, ds, created, columns_synced, sync_warning)。
    created=True 表示本次新建；已存在时仅在无字段或 force_sync 时同步结构。
    """
    ds = db.query(DataSource).filter(DataSource.id == datasource_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if ds.workspace_id != workspace_id:
        raise HTTPException(status_code=400, detail="数据源与工作空间不一致")

    resolved_db = _normalize_meta_db_name(ds, db_name)
    existing = _find_meta_table(
        db,
        workspace_id=workspace_id,
        datasource_id=datasource_id,
        db_name=resolved_db,
        table_name=table_name,
    )
    created = False
    if existing is None:
        table = MetaTable(
            workspace_id=workspace_id,
            datasource_id=datasource_id,
            db_name=resolved_db,
            table_name=table_name,
            table_comment=table_comment,
            table_type=table_type or "table",
            tags=tags,
            owner=owner,
        )
        db.add(table)
        db.commit()
        db.refresh(table)
        created = True
    else:
        table = existing
        dirty = False
        if table_comment and not (table.table_comment or "").strip():
            table.table_comment = table_comment
            dirty = True
        if table_type and table.table_type != table_type:
            table.table_type = table_type
            dirty = True
        if dirty:
            db.commit()
            db.refresh(table)

    col_count = db.query(MetaColumn).filter(MetaColumn.table_id == table.id).count()
    should_sync = force_sync or created or (sync_if_empty and col_count == 0)
    columns_synced = 0
    sync_warning = None
    if should_sync:
        try:
            columns_synced = _sync_table_schema(db, table, ds)
            db.refresh(table)
        except HTTPException as e:
            sync_warning = e.detail if isinstance(e.detail, str) else str(e.detail)
        except Exception as e:
            sync_warning = str(e)

    return table, ds, created, columns_synced, sync_warning


class MetaColumnCreate(BaseModel):
    table_id: int
    column_name: str
    column_type: Optional[str] = None
    column_comment: Optional[str] = None
    is_nullable: bool = True
    is_primary_key: bool = False
    ordinal_position: Optional[int] = None


class LineageCreate(BaseModel):
    src_table_id: int
    dst_table_id: int
    task_node_id: Optional[int] = None


@router.get("/tables")
def search_tables(
    workspace_id: int,
    keyword: Optional[str] = None,
    db_name: Optional[str] = None,
    tag: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_BATCH_DATAMAP_READ)
    q = db.query(MetaTable).filter(MetaTable.workspace_id == workspace_id)
    if keyword:
        q = q.filter(MetaTable.table_name.contains(keyword) | MetaTable.table_comment.contains(keyword))
    if db_name:
        q = q.filter(MetaTable.db_name == db_name)
    tables = q.all()
    result = []
    for t in tables:
        ds = db.query(DataSource).filter(DataSource.id == t.datasource_id).first()
        catalog = _catalog_for_table(ds, t)
        qual = _qualified_name(ds.name, catalog, t.table_name) if ds else f"{catalog}.{t.table_name}".strip(".")
        item = {
            "id": t.id,
            "datasource_id": t.datasource_id,
            "datasource_name": ds.name if ds else None,
            "catalog": catalog or None,
            "qualified_name": qual,
            "db_name": t.db_name, "table_name": t.table_name,
            "table_comment": t.table_comment, "table_type": t.table_type,
            "row_count": t.row_count, "tags": t.tags, "owner": t.owner,
            "last_updated": t.last_updated
        }
        if tag and (not t.tags or tag not in t.tags):
            continue
        result.append(item)
    return result


@router.get("/catalog")
def workspace_catalog(
    workspace_id: int,
    datasource_id: Optional[int] = None,
    keyword: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    数据源内物理表清单 + 已注册元数据合并展示。
    - mysql / doris：默认枚举账号可见的非系统库（可用 extra_config.catalogs 白名单收窄）；
      不再只扫数据源「默认 database」一库。
    - postgresql：psycopg2 + information_schema.tables（默认 schema 为 public，可用 extra_config.schema 覆盖）。
    未注册表可一键注册后走数据字典（数据源.库.表）。
    """
    assert_workspace_data_capability(db, current_user, workspace_id, "viewer", PC.GIDO_BATCH_DATAMAP_READ)
    ds_q = db.query(DataSource).filter(DataSource.workspace_id == workspace_id, DataSource.is_active.is_(True))
    if datasource_id is not None:
        ds_q = ds_q.filter(DataSource.id == datasource_id)
    datasources = ds_q.all()

    meta_by_key: Dict[tuple, MetaTable] = {}
    for mt in db.query(MetaTable).filter(MetaTable.workspace_id == workspace_id).all():
        sch = (mt.db_name or "").strip()
        meta_by_key[(mt.datasource_id, sch, mt.table_name)] = mt

    rows: List[dict] = []
    kw = (keyword or "").strip().lower()

    def _hit_count() -> int:
        return sum(1 for row in rows if row.get("table_name"))

    for ds in datasources:
        if kw and _hit_count() >= _SEARCH_HIT_CAP:
            break
        lt = (ds.ds_type or "").lower()
        if lt not in _CATALOG_DS_TYPES:
            continue

        if lt in ("mysql", "doris"):
            default_schema = (ds.database or "").strip()
            if not default_schema and not _explicit_datamap_catalogs(ds):
                continue
            try:
                import pymysql

                conn_kw: Dict[str, Any] = dict(
                    host=ds.host,
                    port=ds.port or 3306,
                    user=mysql_protocol_connect_user(ds),
                    password=ds.password or "",
                    connect_timeout=15,
                    read_timeout=120,
                    write_timeout=60,
                )
                if default_schema:
                    conn_kw["database"] = default_schema
                conn = pymysql.connect(**conn_kw)
                cur = conn.cursor()
                catalogs = _mysql_catalogs_to_scan(ds, cur)
                scanned_ok = 0
                if kw and catalogs:
                    try:
                        for schema, tn, tt, tc in _fetch_mysql_keyword_tables(cur, catalogs, kw):
                            tc = tc or ""
                            meta = meta_by_key.get((ds.id, schema, tn))
                            qual = _qualified_name(ds.name, schema, tn)
                            rows.append({
                                "row_key": f"{'m' if meta else 'p'}-{ds.id}-{schema}-{tn}",
                                "registered": meta is not None,
                                "meta_table_id": meta.id if meta else None,
                                "datasource_id": ds.id,
                                "datasource_name": ds.name,
                                "ds_type": ds.ds_type,
                                "catalog": schema,
                                "table_name": tn,
                                "qualified_name": qual,
                                "table_comment": (meta.table_comment if meta else tc) or "",
                                "table_type": (meta.table_type if meta else tt) or "table",
                                "row_count": meta.row_count if meta else None,
                                "tags": meta.tags if meta else None,
                                "owner": meta.owner if meta else None,
                                "last_updated": meta.last_updated if meta else None,
                            })
                        scanned_ok = len(catalogs)
                    except Exception as search_err:
                        rows.append({
                            "row_key": f"err-{ds.id}-search",
                            "registered": False,
                            "meta_table_id": None,
                            "datasource_id": ds.id,
                            "datasource_name": ds.name,
                            "error": str(search_err),
                            "qualified_name": f"{ds.name}·搜索",
                            "catalog": "",
                            "table_name": "",
                        })
                else:
                    for schema in catalogs:
                        try:
                            cur.execute(
                                "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES "
                                "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                                (schema,),
                            )
                            for tn, tt, tc in cur.fetchall():
                                tc = tc or ""
                                meta = meta_by_key.get((ds.id, schema, tn))
                                qual = _qualified_name(ds.name, schema, tn)
                                rows.append({
                                    "row_key": f"{'m' if meta else 'p'}-{ds.id}-{schema}-{tn}",
                                    "registered": meta is not None,
                                    "meta_table_id": meta.id if meta else None,
                                    "datasource_id": ds.id,
                                    "datasource_name": ds.name,
                                    "ds_type": ds.ds_type,
                                    "catalog": schema,
                                    "table_name": tn,
                                    "qualified_name": qual,
                                    "table_comment": (meta.table_comment if meta else tc) or "",
                                    "table_type": (meta.table_type if meta else tt) or "table",
                                    "row_count": meta.row_count if meta else None,
                                    "tags": meta.tags if meta else None,
                                    "owner": meta.owner if meta else None,
                                    "last_updated": meta.last_updated if meta else None,
                                })
                            scanned_ok += 1
                        except Exception as schema_err:
                            rows.append({
                                "row_key": f"err-{ds.id}-{schema}",
                                "registered": False,
                                "meta_table_id": None,
                                "datasource_id": ds.id,
                                "datasource_name": ds.name,
                                "error": f"库 {schema}：{schema_err}",
                                "qualified_name": f"{ds.name}.{schema}",
                                "catalog": schema,
                                "table_name": "",
                            })
                if kw and catalogs:
                    try:
                        marks = ",".join(["%s"] * len(catalogs))
                        like = f"%{kw}%"
                        cur.execute(
                            f"SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_COMMENT "
                            f"FROM information_schema.COLUMNS "
                            f"WHERE TABLE_SCHEMA IN ({marks}) AND ("
                            f"LOWER(COLUMN_NAME) LIKE %s OR LOWER(IFNULL(COLUMN_COMMENT,'')) LIKE %s"
                            f") ORDER BY TABLE_SCHEMA, TABLE_NAME LIMIT %s",
                            (*catalogs, like, like, _SEARCH_HIT_CAP),
                        )
                        merge_column_hits(
                            rows,
                            [
                                {
                                    "catalog": item[0],
                                    "table_name": item[1],
                                    "column_name": item[2],
                                    "column_comment": item[3],
                                }
                                for item in cur.fetchall()
                            ],
                            datasource_id=ds.id,
                            datasource_name=ds.name,
                            ds_type=ds.ds_type,
                            meta_by_key=meta_by_key,
                        )
                    except Exception:
                        pass
                if not catalogs:
                    rows.append({
                        "row_key": f"err-{ds.id}-nocat",
                        "registered": False,
                        "meta_table_id": None,
                        "datasource_id": ds.id,
                        "datasource_name": ds.name,
                        "error": "未枚举到任何业务库：请检查数据源默认库 / 库白名单 / 账号可见库权限",
                        "qualified_name": f"{ds.name}.{default_schema or '*'}",
                        "catalog": default_schema or "",
                        "table_name": "",
                    })
                elif kw and scanned_ok and not any(
                    r.get("datasource_id") == ds.id and r.get("table_name") for r in rows
                ):
                    # 有扫库但关键字无命中：给出可操作提示，避免「暂无数据」无因
                    rows.append({
                        "row_key": f"hint-{ds.id}-{kw}",
                        "registered": False,
                        "meta_table_id": None,
                        "datasource_id": ds.id,
                        "datasource_name": ds.name,
                        "error": (
                            f"在已扫 {scanned_ok} 个库中未找到含「{kw}」的表名、注释或字段；"
                            f"已扫：{', '.join(catalogs[:12])}{'…' if len(catalogs) > 12 else ''}。"
                            "可清空关键字看全量，或到数据源核对库白名单是否含 bigdata_dw 等。"
                        ),
                        "qualified_name": f"{ds.name}·搜索",
                        "catalog": "",
                        "table_name": "",
                    })
                conn.close()
            except Exception as e:
                rows.append({
                    "row_key": f"err-{ds.id}",
                    "registered": False,
                    "meta_table_id": None,
                    "datasource_id": ds.id,
                    "datasource_name": ds.name,
                    "error": str(e),
                    "qualified_name": f"{ds.name}.{default_schema or '*'}",
                    "catalog": default_schema or "",
                    "table_name": "",
                })
            continue

        # postgresql
        dbname = (ds.database or "").strip()
        if not dbname:
            continue
        ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
        pg_schema = str(ex.get("schema") or "public").strip() or "public"
        try:
            import psycopg2

            conn = psycopg2.connect(
                host=ds.host or "127.0.0.1",
                port=ds.port or 5432,
                user=(ds.username or "").strip() or None,
                password=ds.password or "",
                dbname=dbname,
                connect_timeout=8,
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY table_name",
                (pg_schema,),
            )
            rows_pg = cur.fetchall()
            for tn, tt in rows_pg:
                tc = ""
                if kw and kw not in tn.lower() and kw not in (tc or "").lower():
                    continue
                catalog_key = f"{pg_schema}"
                meta = meta_by_key.get((ds.id, catalog_key, tn)) or meta_by_key.get((ds.id, pg_schema, tn))
                qual = _qualified_name(ds.name, catalog_key, tn)
                rows.append({
                    "row_key": f"{'m' if meta else 'p'}-{ds.id}-{catalog_key}-{tn}",
                    "registered": meta is not None,
                    "meta_table_id": meta.id if meta else None,
                    "datasource_id": ds.id,
                    "datasource_name": ds.name,
                    "ds_type": ds.ds_type,
                    "catalog": catalog_key,
                    "table_name": tn,
                    "qualified_name": qual,
                    "table_comment": (meta.table_comment if meta else tc) or "",
                    "table_type": (meta.table_type if meta else tt) or "table",
                    "row_count": meta.row_count if meta else None,
                    "tags": meta.tags if meta else None,
                    "owner": meta.owner if meta else None,
                    "last_updated": meta.last_updated if meta else None,
                })
            if kw:
                try:
                    like = f"%{kw}%"
                    cur.execute(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema = %s AND LOWER(column_name) LIKE %s "
                        "ORDER BY table_name LIMIT 80",
                        (pg_schema, like),
                    )
                    merge_column_hits(
                        rows,
                        [
                            {
                                "catalog": pg_schema,
                                "table_name": item[0],
                                "column_name": item[1],
                                "column_comment": "",
                            }
                            for item in cur.fetchall()
                        ],
                        datasource_id=ds.id,
                        datasource_name=ds.name,
                        ds_type=ds.ds_type,
                        meta_by_key=meta_by_key,
                    )
                except Exception:
                    pass
            conn.close()
        except Exception as e:
            rows.append({
                "row_key": f"err-{ds.id}",
                "registered": False,
                "meta_table_id": None,
                "datasource_id": ds.id,
                "datasource_name": ds.name,
                "error": str(e),
                "qualified_name": f"{ds.name}.{dbname}.{pg_schema}",
                "catalog": pg_schema,
                "table_name": "",
            })

    if kw:
        seen_tables = {
            (r.get("datasource_id"), str(r.get("catalog") or "").strip(), r.get("table_name"))
            for r in rows
            if r.get("table_name")
        }
        ds_by_id = {ds.id: ds for ds in datasources}
        meta_q = db.query(MetaTable).filter(MetaTable.workspace_id == workspace_id)
        if datasource_id is not None:
            meta_q = meta_q.filter(MetaTable.datasource_id == datasource_id)
        for mt in meta_q.all():
            tags = mt.tags if isinstance(mt.tags, list) else []
            haystack = " ".join([
                mt.table_name or "",
                mt.business_description or "",
                mt.owner or "",
                " ".join(str(tag) for tag in tags),
            ]).lower()
            if kw not in haystack or not mt.table_name:
                continue
            schema = (mt.db_name or "").strip()
            key = (mt.datasource_id, schema, mt.table_name)
            if key in seen_tables:
                continue
            ds = ds_by_id.get(mt.datasource_id)
            if not ds:
                continue
            rows.append({
                "row_key": f"m-{ds.id}-{schema}-{mt.table_name}",
                "registered": True,
                "meta_table_id": mt.id,
                "datasource_id": ds.id,
                "datasource_name": ds.name,
                "ds_type": ds.ds_type,
                "catalog": schema,
                "table_name": mt.table_name,
                "qualified_name": _qualified_name(ds.name, schema, mt.table_name),
                "table_comment": mt.table_comment or mt.business_description or "",
                "table_type": mt.table_type or "table",
                "row_count": mt.row_count,
                "tags": mt.tags,
                "owner": mt.owner,
                "last_updated": mt.last_updated,
            })
            seen_tables.add(key)

    rows.sort(key=lambda x: (x.get("error") is not None, x.get("qualified_name") or ""))
    if kw:
        tables = [row for row in rows if row.get("table_name")][:_SEARCH_HIT_CAP]
        notes = [row for row in rows if row.get("error") and not row.get("table_name")][:2]
        return tables or notes
    return rows


@router.post("/tables")
def register_table(table_in: MetaTableCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """手动注册元数据表（幂等：已存在则返回并按需同步字段）。"""
    assert_workspace_data_capability(db, current_user, table_in.workspace_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    require_datasource_row(db, current_user, table_in.datasource_id)
    table, _ds, created, columns_synced, sync_warning = _ensure_meta_table(
        db,
        workspace_id=table_in.workspace_id,
        datasource_id=table_in.datasource_id,
        table_name=table_in.table_name,
        db_name=table_in.db_name,
        table_comment=table_in.table_comment,
        table_type=table_in.table_type,
        tags=table_in.tags,
        owner=table_in.owner,
        sync_if_empty=True,
        force_sync=False,
    )
    out = _serialize_table_detail(db, table)
    out["created"] = created
    out["columns_synced"] = columns_synced
    if sync_warning:
        out["sync_warning"] = sync_warning
    return out


@router.post("/ensure-table")
def ensure_table(body: MetaTableEnsure, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """打开/展开目录表时幂等收录：已有 MetaTable 则返回，否则创建并同步字段。"""
    assert_workspace_data_capability(db, current_user, body.workspace_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    require_datasource_row(db, current_user, body.datasource_id)
    table, _ds, created, columns_synced, sync_warning = _ensure_meta_table(
        db,
        workspace_id=body.workspace_id,
        datasource_id=body.datasource_id,
        table_name=body.table_name,
        db_name=body.db_name,
        table_comment=body.table_comment,
        table_type=body.table_type,
        sync_if_empty=body.sync_if_empty,
        force_sync=False,
    )
    out = _serialize_table_detail(db, table)
    out["created"] = created
    out["columns_synced"] = columns_synced
    if sync_warning:
        out["sync_warning"] = sync_warning
    return out


@router.get("/tables/{table_id}")
def get_table_detail(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    table = require_meta_table(db, current_user, table_id)
    return _serialize_table_detail(db, table)


class MetaTableBusinessUpdate(BaseModel):
    owner: Optional[str] = None
    tags: Optional[List[str]] = None
    business_description: Optional[str] = None


def _leaf_table_name(raw: Optional[str]) -> str:
    text = (raw or "").strip().strip("`").strip('"')
    if "." in text:
        text = text.rsplit(".", 1)[-1].strip("`").strip('"')
    return text.lower()


def _sql_mentions_table(sql: Optional[str], table_name: str) -> bool:
    name = (table_name or "").strip()
    if not sql or not name:
        return False
    return re.search(rf"(?i)(?:^|[^0-9A-Za-z_]){re.escape(name)}(?:[^0-9A-Za-z_]|$)", sql) is not None


def migrate_meta_table_business(engine) -> None:
    """业务说明与引擎表注释分开存，同步结构时不会被覆盖。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if not insp.has_table("dw_meta_tables"):
        return
    cols = {col["name"] for col in insp.get_columns("dw_meta_tables")}
    if "business_description" in cols:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE dw_meta_tables ADD COLUMN business_description TEXT"))


def migrate_lineage_producers(engine) -> None:
    """血缘边可以指向同步任务或实时作业，而不只是开发 SQL 节点。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if not insp.has_table("dw_lineage"):
        return
    cols = {col["name"] for col in insp.get_columns("dw_lineage")}
    statements = []
    if "sync_task_id" not in cols:
        statements.append("ALTER TABLE dw_lineage ADD COLUMN sync_task_id INTEGER")
    if "stream_job_id" not in cols:
        statements.append("ALTER TABLE dw_lineage ADD COLUMN stream_job_id INTEGER")
    if not statements:
        return
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


@router.patch("/tables/{table_id}")
def update_table_business(
    table_id: int,
    body: MetaTableBusinessUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """负责人、标签、业务说明。引擎表注释仍由同步结构维护。"""
    table = require_meta_table(db, current_user, table_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    fields = body.model_fields_set
    if "owner" in fields:
        owner = (body.owner or "").strip()
        table.owner = owner[:64] or None
    if "tags" in fields:
        cleaned = []
        for tag in body.tags or []:
            text = str(tag).strip()
            if text and text not in cleaned:
                cleaned.append(text[:32])
            if len(cleaned) >= 12:
                break
        table.tags = cleaned
    if "business_description" in fields:
        note = (body.business_description or "").strip()
        table.business_description = note[:4000] or None
    db.commit()
    db.refresh(table)
    return _serialize_table_detail(db, table)


@router.get("/tables/{table_id}/context")
def table_context(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """这张表被谁生产、谁消费：SQL 任务、同步任务、质量规则、数据服务。"""
    from app.models.data_service import DataApi
    from app.models.workspace import QualityRule, SyncTask
    from app.services.lineage import refresh_lineage_for_table, stream_jobs_for_table

    table = require_meta_table(db, current_user, table_id)
    refresh_lineage_for_table(db, table)
    name = table.table_name

    sql_tasks = []
    by_node: Dict[int, Dict[str, Any]] = {}
    edges = (
        db.query(Lineage)
        .filter((Lineage.src_table_id == table.id) | (Lineage.dst_table_id == table.id))
        .all()
    )
    for edge in edges:
        if not edge.task_node_id:
            continue
        bucket = by_node.setdefault(edge.task_node_id, {"writes": False, "reads": False, "peers": []})
        if edge.dst_table_id == table.id:
            bucket["writes"] = True
            if edge.src_table_id and edge.src_table_id not in bucket["peers"]:
                bucket["peers"].append(edge.src_table_id)
        if edge.src_table_id == table.id:
            bucket["reads"] = True
            if edge.dst_table_id and edge.dst_table_id not in bucket["peers"]:
                bucket["peers"].append(edge.dst_table_id)
    for node_id, bucket in by_node.items():
        node = db.query(TaskNode).filter(TaskNode.id == node_id).first()
        if not node:
            continue
        if bucket["writes"] and bucket["reads"]:
            role = "读写"
        elif bucket["writes"]:
            role = "写入"
        else:
            role = "读取"
        peer_names = []
        for peer_id in bucket["peers"][:3]:
            peer = db.query(MetaTable).filter(MetaTable.id == peer_id).first()
            if peer and peer.table_name:
                peer_names.append(peer.table_name)
        sql_tasks.append({
            "id": node.id,
            "name": node.name,
            "role": role,
            "peer_table": "、".join(peer_names) or None,
        })

    sync_tasks = []
    leaf = name.lower()
    for task in db.query(SyncTask).filter(SyncTask.workspace_id == table.workspace_id).all():
        src_hit = (
            task.src_datasource_id == table.datasource_id
            and _leaf_table_name(task.src_table) == leaf
        )
        dst_hit = (
            task.dst_datasource_id == table.datasource_id
            and _leaf_table_name(task.dst_table) == leaf
        )
        if not src_hit and not dst_hit:
            continue
        sync_tasks.append({
            "id": task.id,
            "name": task.name,
            "role": "同步来源" if src_hit and not dst_hit else "同步目标" if dst_hit and not src_hit else "同步来源和目标",
            "peer_table": task.dst_table if src_hit else task.src_table,
            "last_run_status": task.last_run_status,
        })

    quality_rules = [
        {
            "id": rule.id,
            "name": rule.rule_name,
            "role": rule.rule_type or "质量规则",
            "active": bool(rule.is_active),
        }
        for rule in db.query(QualityRule).filter(QualityRule.table_id == table.id).all()
    ]

    data_apis = []
    for api in db.query(DataApi).filter(DataApi.workspace_id == table.workspace_id).all():
        if api.datasource_id and api.datasource_id != table.datasource_id:
            continue
        wizard = api.wizard_config if isinstance(api.wizard_config, dict) else {}
        wizard_raw = str(wizard.get("table") or "")
        wizard_hit = _leaf_table_name(wizard_raw) == name.lower()
        if wizard_hit and "." in wizard_raw and table.db_name:
            qualifier = wizard_raw.rsplit(".", 1)[0].rsplit(".", 1)[-1].strip("`").strip('"')
            if qualifier and qualifier.lower() != table.db_name.lower():
                wizard_hit = False
        if not wizard_hit and not _sql_mentions_table(api.sql_template, name):
            continue
        data_apis.append({
            "id": api.id,
            "name": api.name,
            "api_code": api.api_code,
            "status": api.status,
            "role": "数据服务",
        })

    return {
        "sql_tasks": sql_tasks,
        "sync_tasks": sync_tasks,
        "quality_rules": quality_rules,
        "data_apis": data_apis,
        "stream_jobs": stream_jobs_for_table(db, table),
    }


@router.post("/tables/{table_id}/columns")
def add_column(table_id: int, col_in: MetaColumnCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_meta_table(db, current_user, table_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    payload = col_in.model_dump()
    if payload.get("table_id") is not None and payload["table_id"] != table_id:
        raise HTTPException(status_code=400, detail="路径与请求体中的表不一致")
    payload["table_id"] = table_id
    col = MetaColumn(**payload)
    db.add(col)
    db.commit()
    db.refresh(col)
    return col


@router.post("/tables/{table_id}/sync-schema")
def sync_schema(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """从数据源同步表结构（MySQL / Doris：DESCRIBE；PostgreSQL：information_schema）"""
    table = require_meta_table(db, current_user, table_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        n = _sync_table_schema(db, table, ds)
        return {"message": "同步成功", "columns": n}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 血缘 ====================

@router.post("/lineage")
def add_lineage(lineage_in: LineageCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    src = require_meta_table(db, current_user, lineage_in.src_table_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    dst = require_meta_table(db, current_user, lineage_in.dst_table_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    if src.workspace_id != dst.workspace_id:
        raise HTTPException(status_code=400, detail="血缘上下游须属于同一工作空间")
    if lineage_in.task_node_id is not None:
        node = require_task_node(db, current_user, lineage_in.task_node_id)
        if node.workspace_id != src.workspace_id:
            raise HTTPException(status_code=400, detail="任务节点与工作空间不一致")
    lineage = Lineage(**lineage_in.model_dump())
    db.add(lineage)
    db.commit()
    db.refresh(lineage)
    return lineage


@router.get("/lineage/{table_id}")
def get_lineage_graph(table_id: int, depth: int = 3, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """获取血缘图谱（上下游）"""
    require_meta_table(db, current_user, table_id)
    table = db.query(MetaTable).filter(MetaTable.id == table_id).first()
    from app.services.lineage import refresh_lineage_for_table

    if table:
        refresh_lineage_for_table(db, table)
    visited = set()
    nodes = {}
    edges = []

    def _get_table_info(tid):
        if tid in nodes:
            return
        t = db.query(MetaTable).filter(MetaTable.id == tid).first()
        if t:
            nodes[tid] = {
                "id": tid,
                "name": f"{t.db_name}.{t.table_name}" if t.db_name else t.table_name,
                "datasource_id": t.datasource_id,
                "db_name": t.db_name,
                "table_name": t.table_name,
            }

    def _traverse_upstream(tid, d):
        if d <= 0 or tid in visited:
            return
        visited.add(tid)
        _get_table_info(tid)
        for lin in db.query(Lineage).filter(Lineage.dst_table_id == tid).all():
            edges.append({
                "source": lin.src_table_id,
                "target": lin.dst_table_id,
                "task_node_id": lin.task_node_id,
                "task_name": _lineage_edge_name(db, lin),
                "sync_task_id": lin.sync_task_id,
                "stream_job_id": lin.stream_job_id,
            })
            _get_table_info(lin.src_table_id)
            _traverse_upstream(lin.src_table_id, d - 1)

    def _traverse_downstream(tid, d):
        if d <= 0 or tid in visited:
            return
        visited.add(tid)
        _get_table_info(tid)
        for lin in db.query(Lineage).filter(Lineage.src_table_id == tid).all():
            edges.append({
                "source": lin.src_table_id,
                "target": lin.dst_table_id,
                "task_node_id": lin.task_node_id,
                "task_name": _lineage_edge_name(db, lin),
                "sync_task_id": lin.sync_task_id,
                "stream_job_id": lin.stream_job_id,
            })
            _get_table_info(lin.dst_table_id)
            _traverse_downstream(lin.dst_table_id, d - 1)

    _traverse_upstream(table_id, depth)
    visited.clear()
    _traverse_downstream(table_id, depth)
    uniq = []
    seen_edges = set()
    for edge in edges:
        key = (edge.get("source"), edge.get("target"), edge.get("task_node_id"))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        uniq.append(edge)
    return {"nodes": list(nodes.values()), "edges": uniq}


@router.get("/lineage/{table_id}/impact")
def get_impact_analysis(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """影响分析：该表变更会影响哪些下游表，以及哪条 SQL 任务在写它们。"""
    table = require_meta_table(db, current_user, table_id)
    from app.services.lineage import refresh_lineage_for_table

    refresh_lineage_for_table(db, table)
    impacted = []
    seen = set()
    queue = [table_id]
    visited = set()
    while queue:
        tid = queue.pop(0)
        if tid in visited:
            continue
        visited.add(tid)
        for lin in db.query(Lineage).filter(Lineage.src_table_id == tid).all():
            key = (lin.dst_table_id, lin.task_node_id)
            t = db.query(MetaTable).filter(MetaTable.id == lin.dst_table_id).first()
            if t and key not in seen and t.id != table_id:
                seen.add(key)
                impacted.append({
                    "table_id": t.id,
                    "table_name": t.table_name,
                    "db_name": t.db_name,
                    "datasource_id": t.datasource_id,
                    "task_node_id": lin.task_node_id,
                    "task_name": _lineage_edge_name(db, lin),
                "sync_task_id": lin.sync_task_id,
                "stream_job_id": lin.stream_job_id,
                })
            queue.append(lin.dst_table_id)
    return {"impacted_tables": impacted}


def _lineage_edge_name(db: Session, edge) -> Optional[str]:
    if edge.task_node_id:
        node = db.query(TaskNode).filter(TaskNode.id == edge.task_node_id).first()
        if node:
            return node.name
    if getattr(edge, "sync_task_id", None):
        from app.models.workspace import SyncTask

        task = db.query(SyncTask).filter(SyncTask.id == edge.sync_task_id).first()
        if task:
            return task.name
    if getattr(edge, "stream_job_id", None):
        try:
            from app.api.streaming import StreamingJob
        except Exception:
            return None
        job = db.query(StreamingJob).filter(StreamingJob.id == edge.stream_job_id).first()
        if job:
            return job.name
    return None


def partition_column_names(summary: Optional[dict]) -> List[str]:
    if not summary:
        return []
    found: List[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(summary.get("expression") or "")):
        if token.lower() in {"range", "list", "hash", "key"}:
            continue
        if token not in found:
            found.append(token)
        if len(found) >= 8:
            break
    return found


def _preview_rows(raw_rows) -> List[List[Any]]:
    from app.services.sql_readonly import json_cell_value

    return [[json_cell_value(value) for value in row] for row in raw_rows]


def _pg_create_table(schema: str, table_name: str, columns: List[dict]) -> str:
    schema = _require_ident(schema, "库名")
    table = _require_ident(table_name, "表名")
    lines = []
    pk = []
    for col in columns:
        name = _require_ident(str(col.get("name") or ""), "字段名")
        col_type = str(col.get("type") or "text").replace(";", " ")
        null_sql = "" if col.get("nullable", True) else " NOT NULL"
        lines.append(f'    "{name}" {col_type}{null_sql}')
        if str(col.get("key") or "").upper() == "PRI":
            pk.append(f'"{name}"')
    if pk:
        lines.append(f"    PRIMARY KEY ({', '.join(pk)})")
    body = ",\n".join(lines) if lines else "    -- 无字段"
    return f'CREATE TABLE "{schema}"."{table}" (\n{body}\n);'


def _mysql_definition(ds: DataSource, catalog: str, table_name: str) -> dict:
    import pymysql

    catalog = _require_ident(catalog, "库名")
    table_name = _require_ident(table_name, "表名")
    conn = pymysql.connect(
        host=ds.host,
        port=ds.port or 3306,
        user=mysql_protocol_connect_user(ds),
        password=ds.password or "",
        database=catalog,
        connect_timeout=15,
        read_timeout=30,
    )
    try:
        cur = conn.cursor()
        cur.execute(f"SHOW CREATE TABLE `{catalog}`.`{table_name}`")
        created = cur.fetchone()
        ddl = created[1] if created and len(created) > 1 else None
        engine = None
        try:
            cur.execute(
                "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                (catalog, table_name),
            )
            engine_row = cur.fetchone()
            engine = engine_row[0] if engine_row else None
        except Exception:
            engine = None
        partitions = None
        for sql in (
            "SELECT PARTITION_NAME, PARTITION_METHOD, PARTITION_EXPRESSION, PARTITION_DESCRIPTION "
            "FROM information_schema.PARTITIONS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND PARTITION_NAME IS NOT NULL "
            "ORDER BY PARTITION_ORDINAL_POSITION",
            "SELECT PARTITION_NAME, PARTITION_METHOD, PARTITION_EXPRESSION, PARTITION_DESCRIPTION "
            "FROM information_schema.PARTITIONS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND PARTITION_NAME IS NOT NULL",
        ):
            try:
                cur.execute(sql, (catalog, table_name))
                partitions = _partition_summary(cur.fetchall())
                break
            except Exception:
                partitions = None
        return {"ddl": ddl, "engine": engine, "partitions": partitions, "ddl_note": None}
    finally:
        conn.close()


@router.get("/tables/{table_id}/definition")
def table_definition(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """建表语句、只读 SELECT、分区摘要。MySQL/Doris 用引擎 DDL；PostgreSQL 由字段生成。"""
    table = require_meta_table(db, current_user, table_id)
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    catalog = (table.db_name or ds.database or "").strip()
    if not catalog:
        raise HTTPException(status_code=400, detail="未配置库名，无法生成语句")
    lt = (ds.ds_type or "").lower()
    try:
        select_sql = build_select_sql(lt, catalog, table.table_name, 100)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        if lt in ("mysql", "doris"):
            detail = _mysql_definition(ds, catalog, table.table_name)
        elif lt == "postgresql":
            from app.services.integration_runtime import list_columns

            ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
            schema = (table.db_name or ex.get("schema") or "public").strip() or "public"
            columns = list_columns(ds, table.table_name, catalog=schema)
            detail = {
                "ddl": _pg_create_table(schema, table.table_name, columns),
                "engine": None,
                "partitions": None,
                "ddl_note": "由字段生成，不含索引和分区细节",
            }
        else:
            raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型: {ds.ds_type}")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取建表语句失败: {e}") from e
    detail["partition_columns"] = partition_column_names(detail.get("partitions"))
    return {"select_sql": select_sql, **detail}


@router.get("/tables/{table_id}/preview")
def preview_table_data(
    table_id: int,
    limit: int = Query(default=100, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """预览表数据（MySQL / Doris / PostgreSQL）"""
    table = require_meta_table(db, current_user, table_id)
    ds = db.query(DataSource).filter(DataSource.id == table.datasource_id).first()
    if not ds:
        raise HTTPException(status_code=404, detail="数据源不存在")
    lt = (ds.ds_type or "").lower()
    try:
        if lt in ("mysql", "doris"):
            import pymysql

            catalog = (table.db_name or ds.database or "").strip()
            if not catalog:
                raise HTTPException(status_code=400, detail="未配置库名，无法预览")
            conn = pymysql.connect(
                host=ds.host,
                port=ds.port or 3306,
                user=mysql_protocol_connect_user(ds),
                password=ds.password or "",
                database=catalog,
            )
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM `{catalog}`.`{table.table_name}` LIMIT %s",
                (limit,),
            )
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()
            return {"columns": columns, "rows": _preview_rows(rows), "total": len(rows)}

        if lt == "postgresql":
            import psycopg2
            from psycopg2 import sql as psql

            dbname = (ds.database or "").strip()
            if not dbname:
                raise HTTPException(status_code=400, detail="未配置数据库名，无法预览")
            ex = ds.extra_config if isinstance(ds.extra_config, dict) else {}
            schema = (table.db_name or ex.get("schema") or "public").strip() or "public"
            conn = psycopg2.connect(
                host=ds.host or "127.0.0.1",
                port=ds.port or 5432,
                user=(ds.username or "").strip() or None,
                password=ds.password or "",
                dbname=dbname,
            )
            cur = conn.cursor()
            q = psql.SQL("SELECT * FROM {}.{} LIMIT %s").format(
                psql.Identifier(schema), psql.Identifier(table.table_name)
            )
            cur.execute(q, (limit,))
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            conn.close()
            return {"columns": columns, "rows": _preview_rows(rows), "total": len(rows)}

        raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型的预览: {ds.ds_type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

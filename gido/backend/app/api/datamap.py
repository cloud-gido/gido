# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime
from app.core.database import get_db
from app.core.security import get_current_user
from app.core import perm_codes as PC
from app.models.workspace import MetaTable, MetaColumn, Lineage, DataSource, User, TaskNode
from app.services.rbac import assert_workspace_data_capability, require_meta_table, require_datasource_row, require_task_node
from app.services.datasource_mysql_user import mysql_protocol_connect_user

router = APIRouter(prefix="/datamap", tags=["数据地图"])

# 数据地图「刷新目录」：从这些类型的数据源拉物理表（与 JDBC 能力一致）
_CATALOG_DS_TYPES = frozenset({"mysql", "doris", "postgresql"})


def _catalog_for_table(ds: Optional[DataSource], table: MetaTable) -> str:
    return (table.db_name or (ds.database if ds else "") or "").strip()


def _qualified_name(ds_name: str, catalog: str, table_name: str) -> str:
    if catalog:
        return f"{ds_name}.{catalog}.{table_name}"
    return f"{ds_name}.{table_name}"


class MetaTableCreate(BaseModel):
    workspace_id: int
    datasource_id: int
    db_name: Optional[str] = None
    table_name: str
    table_comment: Optional[str] = None
    table_type: str = "table"
    tags: Optional[List[str]] = None
    owner: Optional[str] = None


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
    - mysql / doris：PyMySQL + information_schema.TABLES（Doris FE 兼容 MySQL 协议）。
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

    for ds in datasources:
        lt = (ds.ds_type or "").lower()
        if lt not in _CATALOG_DS_TYPES:
            continue

        if lt in ("mysql", "doris"):
            schema = (ds.database or "").strip()
            if not schema:
                continue
            try:
                import pymysql

                conn = pymysql.connect(
                    host=ds.host,
                    port=ds.port or 3306,
                    user=mysql_protocol_connect_user(ds),
                    password=ds.password or "",
                    database=schema,
                    connect_timeout=8,
                )
                cur = conn.cursor()
                cur.execute(
                    "SELECT TABLE_NAME, TABLE_TYPE, TABLE_COMMENT FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                    (schema,),
                )
                for tn, tt, tc in cur.fetchall():
                    tc = tc or ""
                    if kw and kw not in tn.lower() and kw not in tc.lower():
                        continue
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
                conn.close()
            except Exception as e:
                rows.append({
                    "row_key": f"err-{ds.id}",
                    "registered": False,
                    "meta_table_id": None,
                    "datasource_id": ds.id,
                    "datasource_name": ds.name,
                    "error": str(e),
                    "qualified_name": f"{ds.name}.{schema}",
                    "catalog": schema,
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

    rows.sort(key=lambda x: (x.get("error") is not None, x.get("qualified_name") or ""))
    return rows


@router.post("/tables")
def register_table(table_in: MetaTableCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assert_workspace_data_capability(db, current_user, table_in.workspace_id, "developer", PC.GIDO_BATCH_DATAMAP_WRITE)
    ds = require_datasource_row(db, current_user, table_in.datasource_id)
    if ds.workspace_id != table_in.workspace_id:
        raise HTTPException(status_code=400, detail="数据源与工作空间不一致")
    table = MetaTable(**table_in.model_dump())
    db.add(table)
    db.commit()
    db.refresh(table)
    return table


@router.get("/tables/{table_id}")
def get_table_detail(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    table = require_meta_table(db, current_user, table_id)
    columns = db.query(MetaColumn).filter(MetaColumn.table_id == table_id).order_by(MetaColumn.ordinal_position).all()
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
        "db_name": table.db_name, "table_name": table.table_name,
        "table_comment": table.table_comment, "table_type": table.table_type,
        "row_count": table.row_count, "size_bytes": table.size_bytes,
        "tags": table.tags, "owner": table.owner, "last_updated": table.last_updated,
        "columns": [{"id": c.id, "name": c.column_name, "type": c.column_type,
                     "comment": c.column_comment, "nullable": c.is_nullable,
                     "primary_key": c.is_primary_key} for c in columns]
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
    lt = (ds.ds_type or "").lower()
    try:
        if lt in ("mysql", "doris"):
            import pymysql

            catalog = (table.db_name or ds.database or "").strip()
            if not catalog:
                raise HTTPException(status_code=400, detail="请在元数据或数据源上配置库名（catalog），以便定位物理表")
            conn = pymysql.connect(
                host=ds.host,
                port=ds.port or 3306,
                user=mysql_protocol_connect_user(ds),
                password=ds.password or "",
                database=catalog,
            )
            cursor = conn.cursor()
            cursor.execute(f"DESCRIBE `{catalog}`.`{table.table_name}`")
            rows = cursor.fetchall()
            db.query(MetaColumn).filter(MetaColumn.table_id == table_id).delete()
            for i, row in enumerate(rows):
                col = MetaColumn(
                    table_id=table_id,
                    column_name=row[0],
                    column_type=row[1],
                    is_nullable=(row[2] == "YES"),
                    is_primary_key=(row[3] == "PRI"),
                    ordinal_position=i + 1,
                )
                db.add(col)
            cursor.execute(f"SELECT COUNT(*) FROM `{catalog}`.`{table.table_name}`")
            table.row_count = cursor.fetchone()[0]
            table.last_updated = datetime.utcnow()
            conn.close()
            db.commit()
            return {"message": "同步成功", "columns": len(rows)}

        if lt == "postgresql":
            import psycopg2
            from psycopg2 import sql as psql

            dbname = (ds.database or "").strip()
            if not dbname:
                raise HTTPException(status_code=400, detail="PostgreSQL 数据源未配置数据库名")
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
            cur.execute(
                """
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
                """,
                (schema, table.table_name),
            )
            pk_cols = {r[0] for r in cur.fetchall()}
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable, ordinal_position
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, table.table_name),
            )
            col_rows = cur.fetchall()
            db.query(MetaColumn).filter(MetaColumn.table_id == table_id).delete()
            for i, row in enumerate(col_rows):
                cname, dtype, nullable, _ordpos = row[0], row[1], row[2], row[3]
                col = MetaColumn(
                    table_id=table_id,
                    column_name=cname,
                    column_type=dtype or "",
                    is_nullable=(str(nullable).upper() == "YES"),
                    is_primary_key=cname in pk_cols,
                    ordinal_position=i + 1,
                )
                db.add(col)
            cur.execute(
                psql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    psql.Identifier(schema), psql.Identifier(table.table_name)
                )
            )
            table.row_count = cur.fetchone()[0]
            table.last_updated = datetime.utcnow()
            conn.close()
            db.commit()
            return {"message": "同步成功", "columns": len(col_rows)}

        raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型的结构同步: {ds.ds_type}")
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
    visited = set()
    nodes = {}
    edges = []

    def _get_table_info(tid):
        if tid in nodes:
            return
        t = db.query(MetaTable).filter(MetaTable.id == tid).first()
        if t:
            nodes[tid] = {"id": tid, "name": f"{t.db_name}.{t.table_name}" if t.db_name else t.table_name}

    def _traverse_upstream(tid, d):
        if d <= 0 or tid in visited:
            return
        visited.add(tid)
        _get_table_info(tid)
        for lin in db.query(Lineage).filter(Lineage.dst_table_id == tid).all():
            edges.append({"source": lin.src_table_id, "target": lin.dst_table_id})
            _get_table_info(lin.src_table_id)
            _traverse_upstream(lin.src_table_id, d - 1)

    def _traverse_downstream(tid, d):
        if d <= 0 or tid in visited:
            return
        visited.add(tid)
        _get_table_info(tid)
        for lin in db.query(Lineage).filter(Lineage.src_table_id == tid).all():
            edges.append({"source": lin.src_table_id, "target": lin.dst_table_id})
            _get_table_info(lin.dst_table_id)
            _traverse_downstream(lin.dst_table_id, d - 1)

    _traverse_upstream(table_id, depth)
    visited.clear()
    _traverse_downstream(table_id, depth)

    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/lineage/{table_id}/impact")
def get_impact_analysis(table_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """影响分析：该表变更会影响哪些下游"""
    require_meta_table(db, current_user, table_id)
    impacted = []
    queue = [table_id]
    visited = set()
    while queue:
        tid = queue.pop(0)
        if tid in visited:
            continue
        visited.add(tid)
        for lin in db.query(Lineage).filter(Lineage.src_table_id == tid).all():
            t = db.query(MetaTable).filter(MetaTable.id == lin.dst_table_id).first()
            if t:
                impacted.append({"table_id": t.id, "table_name": t.table_name, "db_name": t.db_name})
            queue.append(lin.dst_table_id)
    return {"impacted_tables": impacted}


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
            columns = [d[0] for d in cursor.description]
            conn.close()
            return {"columns": columns, "rows": [list(row) for row in rows], "total": len(rows)}

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
            columns = [d[0] for d in cur.description]
            conn.close()
            return {"columns": columns, "rows": [list(row) for row in rows], "total": len(rows)}

        raise HTTPException(status_code=400, detail=f"暂不支持该数据源类型的预览: {ds.ds_type}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

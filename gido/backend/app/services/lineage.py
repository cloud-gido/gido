# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
血缘自动解析：从 SQL 脚本中提取 INSERT INTO ... SELECT ... 的表级血缘。

字典行是血缘边的锚点，不是用户要先做的「收录」。解析时按任务数据源补齐缺失的表，
这样工作流跑完就能在数据地图里看到上下游，不必先打开每张表。
"""
import re
from typing import List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session


def auto_parse_lineage(node, db: Session, *, commit: bool = True):
    """解析 SQL 节点的血缘关系并写入 dw_lineage。按语句配对，避免多条 INSERT 互相串边。"""
    from app.models.workspace import Lineage

    if getattr(node, "node_type", None) != "SQL" or not node.script_content:
        return

    datasource_id = getattr(node, "datasource_id", None)
    for dst_db, dst_name, sources in _statement_edges(node.script_content):
        dst = _ensure_lineage_table(
            db, node.workspace_id, dst_name, datasource_id, dst_db,
        )
        if not dst:
            continue
        for src_db, src_name in sources:
            if src_name == dst_name and (not src_db or not dst_db or src_db == dst_db):
                continue
            src = _ensure_lineage_table(
                db, node.workspace_id, src_name, datasource_id, src_db,
            )
            if not src or src.id == dst.id:
                continue
            exists = db.query(Lineage).filter(
                Lineage.src_table_id == src.id,
                Lineage.dst_table_id == dst.id,
                Lineage.task_node_id == node.id,
            ).first()
            if not exists:
                db.add(Lineage(src_table_id=src.id, dst_table_id=dst.id, task_node_id=node.id))
    if commit:
        db.commit()


def _contains_name(column, name: str):
    """按字面量包含过滤，下划线不当成通配符。数据库里筛掉无关脚本，少传正文。"""
    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(column).like(f"%{escaped}%", escape="\\")


def refresh_lineage_for_table(db: Session, table) -> None:
    """打开血缘/影响分析时，用已保存的 SQL、同步任务和实时作业补边。"""
    from app.models.workspace import TaskNode

    name = (getattr(table, "table_name", None) or "").strip().lower()
    workspace_id = getattr(table, "workspace_id", None)
    if not name or not workspace_id:
        return
    nodes = (
        db.query(TaskNode)
        .filter(
            TaskNode.workspace_id == workspace_id,
            TaskNode.node_type == "SQL",
            _contains_name(TaskNode.script_content, name),
        )
        .all()
    )
    touched = False
    for node in nodes:
        sql = node.script_content or ""
        if name not in sql.lower():
            continue
        auto_parse_lineage(node, db, commit=False)
        touched = True
    if _refresh_sync_lineage(db, table):
        touched = True
    if _refresh_stream_lineage(db, table):
        touched = True
    if touched:
        db.commit()


def _split_table_ref(raw: Optional[str]) -> Tuple[Optional[str], str]:
    text = (raw or "").strip().strip("`").strip('"')
    if "." in text:
        catalog, name = text.rsplit(".", 1)
        return catalog.strip("`").strip('"').lower() or None, name.strip("`").strip('"').lower()
    return None, text.lower()


def _table_matches(table, catalog: Optional[str], name: str) -> bool:
    if (table.table_name or "").strip().lower() != (name or "").strip().lower():
        return False
    if catalog and (table.db_name or "").strip().lower() not in ("", catalog.lower()):
        return False
    return True


def _refresh_sync_lineage(db: Session, table) -> bool:
    """同步任务的来源表和目标表是明确的，直接落边。"""
    from sqlalchemy import and_, or_

    from app.models.workspace import Lineage, SyncTask

    touched = False
    name = (table.table_name or "").lower()
    tasks = db.query(SyncTask).filter(
        SyncTask.workspace_id == table.workspace_id,
        or_(
            and_(SyncTask.src_datasource_id == table.datasource_id, _contains_name(SyncTask.src_table, name)),
            and_(SyncTask.dst_datasource_id == table.datasource_id, _contains_name(SyncTask.dst_table, name)),
        ),
    ).all()
    for task in tasks:
        src_catalog, src_name = _split_table_ref(task.src_table)
        dst_catalog, dst_name = _split_table_ref(task.dst_table)
        src_hit = task.src_datasource_id == table.datasource_id and src_name == (table.table_name or "").lower()
        dst_hit = task.dst_datasource_id == table.datasource_id and dst_name == (table.table_name or "").lower()
        if not src_hit and not dst_hit:
            continue
        if src_hit and (not src_catalog or src_catalog == (table.db_name or "").lower()):
            src = table
        else:
            src = _ensure_lineage_table(db, table.workspace_id, src_name, task.src_datasource_id, src_catalog)
        if dst_hit and (not dst_catalog or dst_catalog == (table.db_name or "").lower()):
            dst = table
        else:
            dst = _ensure_lineage_table(db, table.workspace_id, dst_name, task.dst_datasource_id, dst_catalog)
        if not src or not dst or src.id == dst.id:
            continue
        exists = db.query(Lineage).filter(
            Lineage.src_table_id == src.id,
            Lineage.dst_table_id == dst.id,
            Lineage.sync_task_id == task.id,
        ).first()
        if exists:
            continue
        db.add(Lineage(
            src_table_id=src.id,
            dst_table_id=dst.id,
            sync_task_id=task.id,
            lineage_type="sync",
        ))
        touched = True
    return touched


def _known_table(db: Session, anchor, catalog: Optional[str], name: str):
    """实时 SQL 只连接已经在字典里的表，避免把 Kafka topic 写成数仓表。"""
    from app.models.workspace import MetaTable

    if _table_matches(anchor, catalog, name):
        return anchor
    rows = (
        db.query(MetaTable)
        .filter(
            MetaTable.workspace_id == anchor.workspace_id,
            func.lower(MetaTable.table_name) == (name or "").lower(),
        )
        .all()
    )
    if catalog:
        for row in rows:
            if (row.db_name or "").strip().lower() == catalog.lower():
                return row
        return None
    for row in rows:
        if row.datasource_id == anchor.datasource_id:
            return row
    return rows[0] if rows else None


def _refresh_stream_lineage(db: Session, table) -> bool:
    from app.models.workspace import Lineage

    try:
        from app.api.streaming import StreamingJob
    except Exception:
        return False
    touched = False
    name = (table.table_name or "").lower()
    try:
        jobs = db.query(StreamingJob).filter(
            StreamingJob.workspace_id == table.workspace_id,
            StreamingJob.job_type == "SQL",
            _contains_name(StreamingJob.script_content, name),
        ).all()
    except Exception:
        return False
    for job in jobs:
        sql = job.script_content or ""
        if name not in sql.lower():
            continue
        for dst_catalog, dst_name, sources in _statement_edges(sql):
            dst = _known_table(db, table, dst_catalog, dst_name)
            if not dst:
                continue
            for src_catalog, src_name in sources:
                src = _known_table(db, table, src_catalog, src_name)
                if not src or src.id == dst.id:
                    continue
                exists = db.query(Lineage).filter(
                    Lineage.src_table_id == src.id,
                    Lineage.dst_table_id == dst.id,
                    Lineage.stream_job_id == job.id,
                ).first()
                if exists:
                    continue
                db.add(Lineage(
                    src_table_id=src.id,
                    dst_table_id=dst.id,
                    stream_job_id=job.id,
                    lineage_type="stream",
                ))
                touched = True
    return touched


def stream_jobs_for_table(db: Session, table) -> List[dict]:
    """实时作业只要脚本提到这张表就列出来，不要求对端也在字典里。"""
    try:
        from app.api.streaming import StreamingJob
    except Exception:
        return []
    name = (table.table_name or "").lower()
    if not name:
        return []
    found = []
    try:
        jobs = db.query(StreamingJob).filter(
            StreamingJob.workspace_id == table.workspace_id,
            StreamingJob.job_type == "SQL",
            _contains_name(StreamingJob.script_content, name),
        ).all()
    except Exception:
        return []
    for job in jobs:
        sql = job.script_content or ""
        if name not in sql.lower():
            continue
        writes = False
        reads = False
        for dst_catalog, dst_name, sources in _statement_edges(sql):
            if _table_matches(table, dst_catalog, dst_name):
                writes = True
            if any(_table_matches(table, src_catalog, src_name) for src_catalog, src_name in sources):
                reads = True
        if writes and reads:
            role = "读写"
        elif writes:
            role = "写入"
        elif reads:
            role = "读取"
        else:
            role = "引用"
        found.append({
            "id": job.id,
            "name": job.name,
            "role": role,
            "status": job.status,
        })
    return found


_IDENT = r"(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][\w$]*)"
_QUALIFIED = rf"({_IDENT})(?:\s*\.\s*({_IDENT}))?"
_INSERT_RE = re.compile(
    rf"INSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?{_QUALIFIED}",
    re.IGNORECASE,
)
_CTAS_RE = re.compile(
    rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_QUALIFIED}\s+AS\b",
    re.IGNORECASE,
)
_FROM_RE = re.compile(
    rf"(?:FROM|JOIN)\s+{_QUALIFIED}",
    re.IGNORECASE,
)
_CTE_RE = re.compile(
    rf"(?:WITH|,)\s+({_IDENT})\s+AS\s*\(",
    re.IGNORECASE,
)
_SKIP_SOURCES = {"select", "where", "on", "and", "or", "lateral", "unnest", "values"}


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def _statements(sql: str) -> List[str]:
    raw = _strip_sql_comments(sql or "")
    parts: List[str] = []
    buf: List[str] = []
    in_sq = False
    in_dq = False
    in_bt = False
    for ch in raw:
        if ch == "'" and not in_dq and not in_bt:
            in_sq = not in_sq
        elif ch == '"' and not in_sq and not in_bt:
            in_dq = not in_dq
        elif ch == "`" and not in_sq and not in_dq:
            in_bt = not in_bt
        if ch == ";" and not in_sq and not in_dq and not in_bt:
            stmt = "".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _unquote_ident(token: str) -> str:
    text = (token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "`\"'":
        text = text[1:-1]
    return text.strip().lower()


def _qualified(match) -> Tuple[Optional[str], str]:
    first = _unquote_ident(match[0])
    second = _unquote_ident(match[1]) if match[1] else ""
    if second:
        return first, second
    return None, first


def _statement_edges(sql: str) -> List[Tuple[Optional[str], str, List[Tuple[Optional[str], str]]]]:
    edges = []
    for stmt in _statements(sql):
        targets = [_qualified(m) for m in _INSERT_RE.findall(stmt)]
        targets.extend(_qualified(m) for m in _CTAS_RE.findall(stmt))
        if not targets:
            continue
        cte_names = {_unquote_ident(name) for name in _CTE_RE.findall(stmt)}
        sources: List[Tuple[Optional[str], str]] = []
        seen = set()
        for match in _FROM_RE.findall(stmt):
            catalog, name = _qualified(match)
            if not name or name in _SKIP_SOURCES or name in cte_names:
                continue
            key = (catalog or "", name)
            if key in seen:
                continue
            seen.add(key)
            sources.append((catalog, name))
        for catalog, name in targets:
            if name:
                edges.append((catalog, name, sources))
    return edges


def _extract_insert_tables(sql: str) -> List[Tuple[Optional[str], str]]:
    return [(catalog, name) for catalog, name, _sources in _statement_edges(sql)]


def _extract_source_tables(sql: str) -> List[Tuple[Optional[str], str]]:
    seen = set()
    result: List[Tuple[Optional[str], str]] = []
    for _catalog, _name, sources in _statement_edges(sql):
        for item in sources:
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
    return result


def _ensure_lineage_table(
    db: Session,
    workspace_id: int,
    table_name: str,
    datasource_id: Optional[int],
    db_name: Optional[str],
):
    """找到已有字典表；没有且任务带了数据源时补一行，供血缘边挂接。"""
    from app.models.workspace import DataSource, MetaTable

    name = (table_name or "").strip()
    if not name or not workspace_id:
        return None
    rows = (
        db.query(MetaTable)
        .filter(
            MetaTable.workspace_id == workspace_id,
            func.lower(MetaTable.table_name) == name.lower(),
        )
        .all()
    )
    if datasource_id:
        scoped = [row for row in rows if row.datasource_id == datasource_id]
    else:
        scoped = rows
    want_db = (db_name or "").strip().lower()
    if want_db:
        for row in scoped:
            if (row.db_name or "").strip().lower() == want_db:
                return row
    if scoped:
        return scoped[0]
    if not datasource_id:
        return None
    if not want_db:
        ds = db.query(DataSource).filter(DataSource.id == datasource_id).first()
        db_name = (ds.database or "").strip() or None if ds else None
    table = MetaTable(
        workspace_id=workspace_id,
        datasource_id=datasource_id,
        db_name=db_name,
        table_name=name,
        table_type="table",
    )
    db.add(table)
    db.flush()
    return table

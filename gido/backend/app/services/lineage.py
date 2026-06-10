# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
"""
血缘自动解析：从 SQL 脚本中提取 INSERT INTO ... SELECT ... 的表级血缘
"""
import re
from sqlalchemy.orm import Session


def auto_parse_lineage(node, db: Session):
    """解析 SQL 节点的血缘关系并写入 dw_lineage"""
    from app.models.workspace import Lineage, MetaTable
    if node.node_type != "SQL" or not node.script_content:
        return

    sql = node.script_content
    # 匹配 INSERT INTO <dst> ... SELECT ... FROM <src> [JOIN <src2>]
    dst_tables = _extract_insert_tables(sql)
    src_tables = _extract_source_tables(sql)

    for dst_name in dst_tables:
        dst = _find_or_none(db, node.workspace_id, dst_name)
        if not dst:
            continue
        for src_name in src_tables:
            if src_name == dst_name:
                continue
            src = _find_or_none(db, node.workspace_id, src_name)
            if not src:
                continue
            exists = db.query(Lineage).filter(
                Lineage.src_table_id == src.id,
                Lineage.dst_table_id == dst.id,
                Lineage.task_node_id == node.id
            ).first()
            if not exists:
                db.add(Lineage(src_table_id=src.id, dst_table_id=dst.id, task_node_id=node.id))
    db.commit()


def _extract_insert_tables(sql: str) -> list:
    pattern = r'INSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?`?(\w+)`?(?:\.`?(\w+)`?)?'
    matches = re.findall(pattern, sql, re.IGNORECASE)
    result = []
    for m in matches:
        name = m[1] if m[1] else m[0]
        result.append(name.lower())
    return result


def _extract_source_tables(sql: str) -> list:
    pattern = r'(?:FROM|JOIN)\s+`?(\w+)`?(?:\.`?(\w+)`?)?(?:\s+(?:AS\s+)?\w+)?'
    matches = re.findall(pattern, sql, re.IGNORECASE)
    result = []
    for m in matches:
        name = m[1] if m[1] else m[0]
        if name.upper() not in ('SELECT', 'WHERE', 'ON', 'AND', 'OR'):
            result.append(name.lower())
    return list(set(result))


def _find_or_none(db: Session, workspace_id: int, table_name: str):
    from app.models.workspace import MetaTable
    return db.query(MetaTable).filter(
        MetaTable.workspace_id == workspace_id,
        MetaTable.table_name == table_name
    ).first()

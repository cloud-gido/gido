/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 库 → 表 → 列 树节点拼装（Studio 库表抽屉 / 数据地图共用逻辑）。
 */
import type { DataNode } from 'antd/es/tree'
import type { ColumnHint, TableHint } from './sqlSchemaCache'

export type SchemaNodeMeta =
  | { kind: 'schema'; name: string; isDefault?: boolean }
  | { kind: 'table'; catalog: string; name: string; comment?: string; registered?: boolean; metaTableId?: number }
  | { kind: 'column'; catalog: string; table: string; name: string; type?: string; comment?: string | null }

export type SchemaTreeNode = DataNode & {
  meta?: SchemaNodeMeta
}

export function schemaKey(name: string) {
  return `s:${name}`
}

export function tableKey(catalog: string, name: string) {
  return `t:${catalog}:${name}`
}

export function columnKey(catalog: string, table: string, name: string) {
  return `c:${catalog}:${table}:${name}`
}

export function attachTables(
  prev: SchemaTreeNode[],
  catalog: string,
  tables: TableHint[],
  registeredLookup?: (catalog: string, table: string) => { id: number } | undefined,
): SchemaTreeNode[] {
  return prev.map((n) => {
    if (n.key !== schemaKey(catalog)) return n
    return {
      ...n,
      children: tables.map((t) => {
        const reg = registeredLookup?.(catalog, t.name)
        return {
          key: tableKey(catalog, t.name),
          title: t.name,
          isLeaf: false,
          meta: {
            kind: 'table' as const,
            catalog,
            name: t.name,
            comment: t.comment || undefined,
            registered: Boolean(reg),
            metaTableId: reg?.id,
          },
        }
      }),
    }
  })
}

export function attachColumns(
  prev: SchemaTreeNode[],
  catalog: string,
  table: string,
  cols: ColumnHint[],
): SchemaTreeNode[] {
  const tKey = tableKey(catalog, table)
  return prev.map((n) => {
    if (n.key !== schemaKey(catalog) || !n.children) return n
    return {
      ...n,
      children: n.children.map((ch) => {
        if (ch.key !== tKey) return ch
        return {
          ...ch,
          children: cols.map((c) => ({
            key: columnKey(catalog, table, c.name),
            title: c.name,
            isLeaf: true,
            meta: {
              kind: 'column' as const,
              catalog,
              table,
              name: c.name,
              type: c.type || undefined,
              comment: c.comment,
            },
          })),
        }
      }),
    }
  })
}

export function patchTableRegistered(
  prev: SchemaTreeNode[],
  catalog: string,
  table: string,
  metaTableId: number,
): SchemaTreeNode[] {
  const tKey = tableKey(catalog, table)
  return prev.map((n) => {
    if (n.key !== schemaKey(catalog) || !n.children) return n
    return {
      ...n,
      children: n.children.map((ch) => {
        if (ch.key !== tKey) return ch
        const meta = ch.meta && ch.meta.kind === 'table' ? ch.meta : null
        return {
          ...ch,
          meta: meta
            ? { ...meta, registered: true, metaTableId }
            : { kind: 'table' as const, catalog, name: table, registered: true, metaTableId },
        }
      }),
    }
  })
}

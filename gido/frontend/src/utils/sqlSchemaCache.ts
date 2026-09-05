/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * SQL 库/表/列补全缓存（Studio / Probe / 库表抽屉共用）。
 */
import { integrationApi } from '../api'

export type SchemaHint = { name: string; is_default?: boolean }
export type TableHint = { name: string; type?: string; comment?: string; catalog?: string }
export type ColumnHint = {
  name: string
  type?: string
  nullable?: boolean
  key?: string
  catalog?: string
  table?: string
}

type CacheEntry<T> = { at: number; value: T }

const TTL_MS = 5 * 60 * 1000

const schemaCache = new Map<number, CacheEntry<SchemaHint[]>>()
const tableCache = new Map<string, CacheEntry<TableHint[]>>()
const columnCache = new Map<string, CacheEntry<ColumnHint[]>>()

function fresh<T>(entry: CacheEntry<T> | undefined): T | null {
  if (!entry) return null
  if (Date.now() - entry.at > TTL_MS) return null
  return entry.value
}

function tableKey(datasourceId: number, catalog: string | null | undefined): string {
  return `${datasourceId}::${(catalog || '').trim().toLowerCase()}`
}

function columnKey(datasourceId: number, catalog: string | null | undefined, table: string): string {
  return `${datasourceId}::${(catalog || '').trim().toLowerCase()}::${table.trim().toLowerCase()}`
}

export function invalidateSqlSchemaCache(datasourceId?: number): void {
  if (datasourceId == null) {
    schemaCache.clear()
    tableCache.clear()
    columnCache.clear()
    return
  }
  schemaCache.delete(datasourceId)
  for (const k of [...tableCache.keys()]) {
    if (k.startsWith(`${datasourceId}::`)) tableCache.delete(k)
  }
  for (const k of [...columnCache.keys()]) {
    if (k.startsWith(`${datasourceId}::`)) columnCache.delete(k)
  }
}

export async function fetchSchemas(datasourceId: number, keyword = ''): Promise<SchemaHint[]> {
  const cached = fresh(schemaCache.get(datasourceId))
  if (cached && !keyword) return cached
  const res: any = await integrationApi.listSchemas(datasourceId, keyword || undefined)
  const list: SchemaHint[] = Array.isArray(res?.schemas) ? res.schemas : []
  if (!keyword) schemaCache.set(datasourceId, { at: Date.now(), value: list })
  return list
}

export async function fetchTables(
  datasourceId: number,
  catalog?: string | null,
  keyword = '',
): Promise<TableHint[]> {
  const key = tableKey(datasourceId, catalog)
  const cached = fresh(tableCache.get(key))
  if (cached && !keyword) return cached
  const res: any = await integrationApi.listTables(datasourceId, keyword || '', catalog || undefined)
  const list: TableHint[] = Array.isArray(res?.tables) ? res.tables : []
  if (!keyword) tableCache.set(key, { at: Date.now(), value: list })
  return list
}

export async function fetchColumns(
  datasourceId: number,
  tableName: string,
  catalog?: string | null,
): Promise<ColumnHint[]> {
  const key = columnKey(datasourceId, catalog, tableName)
  const cached = fresh(columnCache.get(key))
  if (cached) return cached
  const res: any = await integrationApi.getColumns(datasourceId, tableName, catalog || undefined)
  const list: ColumnHint[] = Array.isArray(res?.columns) ? res.columns : []
  columnCache.set(key, { at: Date.now(), value: list })
  return list
}

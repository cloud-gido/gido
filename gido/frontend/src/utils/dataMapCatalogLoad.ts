/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图目录合并与可枚举数据源筛选（与后端 _CATALOG_DS_TYPES 对齐）。
 * 首屏用已注册元数据，再按数据源渐进拉物理 catalog（业界主流数据开发台分源懒加载）。
 * rowKey 用「数据源|库|表」稳定键，避免注册壳换成物理行时表格整行重挂导致闪一下。
 */

export const DATAMAP_CATALOG_DS_TYPES = new Set(['mysql', 'doris', 'postgresql'])

export function isCatalogCapableDatasource(ds: { ds_type?: string; is_active?: boolean } | null | undefined): boolean {
  if (!ds) return false
  if (ds.is_active === false) return false
  return DATAMAP_CATALOG_DS_TYPES.has(String(ds.ds_type || '').toLowerCase())
}

export function catalogCapableDatasources<T extends { id: number; ds_type?: string; is_active?: boolean }>(
  list: T[],
  dsFilter?: number | null,
): T[] {
  const capable = list.filter(isCatalogCapableDatasource)
  if (dsFilter != null) return capable.filter(d => d.id === dsFilter)
  return capable
}

export function tableKeyOf(dsid: number | string, cat: string, tn: string) {
  return `${Number(dsid)}|${String(cat || '').trim()}|${String(tn || '').trim()}`
}

function stableRowKey(row: any, fallbackIndex: number): string {
  if (row?.error && row.datasource_id != null) return `err-${Number(row.datasource_id)}`
  if (row?.datasource_id != null && row?.table_name) {
    return tableKeyOf(row.datasource_id, row.catalog || '', row.table_name || '')
  }
  return String(row?.row_key ?? row?.rowKey ?? `row-${fallbackIndex}`)
}

/** 已注册元数据转目录行（物理 catalog 尚未覆盖时的壳） */
export function registeredToCatalogExtras(
  registered: any[],
  dsFilter?: number | null,
): any[] {
  const extras: any[] = []
  for (const t of registered) {
    if (dsFilter != null && t.datasource_id !== dsFilter) continue
    const cat = String(t.catalog || t.db_name || '')
    const tn = String(t.table_name || '')
    if (!tn) continue
    const k = tableKeyOf(t.datasource_id, cat, tn)
    extras.push({
      row_key: k,
      registered: true,
      meta_table_id: t.id,
      datasource_id: t.datasource_id,
      datasource_name: t.datasource_name || '—',
      catalog: cat || null,
      table_name: tn,
      qualified_name: t.qualified_name || k,
      table_comment: t.table_comment,
      table_type: t.table_type,
      row_count: t.row_count,
      tags: t.tags,
      owner: t.owner,
      last_updated: t.last_updated,
    })
  }
  return extras
}

/**
 * 物理 catalog 行 + 已注册补齐：catalog 命中则不重复追加注册行。
 */
export function mergeCatalogWithRegistered(
  catalogRows: any[],
  registered: any[],
  dsFilter?: number | null,
): any[] {
  const seen = new Set<string>()
  const normalizedCatalog: any[] = []
  for (const x of catalogRows) {
    if (x.error) {
      normalizedCatalog.push({ ...x, row_key: `err-${Number(x.datasource_id)}` })
      continue
    }
    if (x.datasource_id == null || !x.table_name) continue
    const k = tableKeyOf(x.datasource_id, x.catalog || '', x.table_name || '')
    seen.add(k)
    normalizedCatalog.push({ ...x, row_key: k })
  }
  const extras = registeredToCatalogExtras(registered, dsFilter).filter((row) => {
    const k = tableKeyOf(row.datasource_id, row.catalog || '', row.table_name || '')
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
  return [...normalizedCatalog, ...extras].map((row: any, i: number) => ({
    ...row,
    rowKey: stableRowKey(row, i),
  }))
}

/** 用某一数据源的新 catalog 结果替换合并缓冲里该源旧行 */
export function replaceDatasourceCatalogRows(
  prevCatalogRows: any[],
  datasourceId: number,
  nextRows: any[],
): any[] {
  const kept = prevCatalogRows.filter(r => Number(r.datasource_id) !== datasourceId)
  return [...kept, ...(Array.isArray(nextRows) ? nextRows : [])]
}

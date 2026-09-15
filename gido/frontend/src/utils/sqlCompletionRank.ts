/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * SQL 补全排序 / 最近使用 / 列元数据展示（成熟 SaaS 编辑器心智）。
 */
const RECENT_STORAGE_KEY = 'gido.sql.completion.recent.v1'
const RECENT_MAX = 24

export type SqlRecentKind = 'table' | 'column' | 'schema'

type RecentStore = {
  tables: string[]
  columns: string[]
  schemas: string[]
}

function emptyStore(): RecentStore {
  return { tables: [], columns: [], schemas: [] }
}

function loadStore(): RecentStore {
  try {
    const raw = localStorage.getItem(RECENT_STORAGE_KEY)
    if (!raw) return emptyStore()
    const parsed = JSON.parse(raw) as Partial<RecentStore>
    return {
      tables: Array.isArray(parsed.tables) ? parsed.tables.map(String) : [],
      columns: Array.isArray(parsed.columns) ? parsed.columns.map(String) : [],
      schemas: Array.isArray(parsed.schemas) ? parsed.schemas.map(String) : [],
    }
  } catch {
    return emptyStore()
  }
}

function saveStore(store: RecentStore) {
  try {
    localStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(store))
  } catch {
    /* ignore quota */
  }
}

export function markSqlCompletionRecent(kind: SqlRecentKind, name: string): void {
  const n = String(name || '').trim()
  if (!n) return
  const store = loadStore()
  const key = kind === 'table' ? 'tables' : kind === 'column' ? 'columns' : 'schemas'
  const next = [n, ...store[key].filter(x => x.toLowerCase() !== n.toLowerCase())].slice(0, RECENT_MAX)
  store[key] = next
  saveStore(store)
}

export function isSqlCompletionRecent(kind: SqlRecentKind, name: string): boolean {
  const n = String(name || '').trim().toLowerCase()
  if (!n) return false
  const store = loadStore()
  const list = kind === 'table' ? store.tables : kind === 'column' ? store.columns : store.schemas
  return list.some(x => x.toLowerCase() === n || x.toLowerCase().endsWith(`.${n}`))
}

/**
 * Monaco sortText：字典序越小越靠前。
 * tier: col/cte < table < schema/fn < kw
 */
export function completionSortText(opts: {
  tier: 'col' | 'cte' | 'table' | 'schema' | 'fn' | 'kw'
  name: string
  prefix: string
  recent?: boolean
}): string {
  const tierMap: Record<typeof opts.tier, string> = {
    col: '0',
    cte: '0',
    table: '1',
    schema: '2',
    fn: '2',
    kw: '3',
  }
  const p = (opts.prefix || '').toLowerCase()
  const n = (opts.name || '').toLowerCase()
  let match = '5'
  if (p) {
    if (n.startsWith(p)) match = '0'
    else if (n.includes(p)) match = '2'
    else match = '8'
  }
  const recent = opts.recent ? '0' : '1'
  return `${tierMap[opts.tier]}${recent}${match}_${opts.name}`
}

export function formatColumnDetail(c: {
  type?: string
  key?: string
  nullable?: boolean
  comment?: string | null
  table?: string
}): string {
  const parts: string[] = []
  const key = (c.key || '').toUpperCase()
  if (key === 'PRI' || key === 'PRIMARY') parts.push('PK')
  else if (key === 'UNI' || key === 'UNIQUE') parts.push('UK')
  else if (key === 'MUL') parts.push('IDX')
  if (c.type) parts.push(String(c.type))
  if (c.nullable === false) parts.push('NOT NULL')
  if (c.comment) parts.push(String(c.comment).slice(0, 48))
  else if (c.table) parts.push(c.table)
  return parts.join(' · ') || 'column'
}

/** 前缀过滤：优先 startsWith，其次 includes；空前缀原样返回（调用方可再 cap）。 */
export function filterByPrefixRanked<T extends { name: string }>(items: T[], prefix: string): T[] {
  const p = prefix.toLowerCase()
  if (!p) return items
  const starts: T[] = []
  const includes: T[] = []
  for (const i of items) {
    const n = i.name.toLowerCase()
    if (n.startsWith(p)) starts.push(i)
    else if (n.includes(p)) includes.push(i)
  }
  return [...starts, ...includes]
}

export function filterNamesRanked(names: string[], prefix: string): string[] {
  return filterByPrefixRanked(
    names.map(name => ({ name })),
    prefix,
  ).map(x => x.name)
}

/** 大库防洪水：无前缀时截断，有前缀时放宽。 */
export function capSuggestions<T>(items: T[], prefix: string, softCap = 120, hardCap = 240): T[] {
  const cap = prefix.trim() ? hardCap : softCap
  return items.length > cap ? items.slice(0, cap) : items
}

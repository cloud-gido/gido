/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 查询结果单元格比较 / 排序：对齐 DataWorks 升序·降序。
 * 先 O(n) 预计算可比键再 sort，避免每次比较都跑正则 / localeCompare（1 万行也不易卡死主线程）。
 */
export function isQueryNullValue(v: unknown): boolean {
  return v == null || v === '' || v === 'None'
}

export type QuerySortOrder = 'ascend' | 'descend'

/** 预计算后的排序键：null 沉底；数字优先；其余为字符串 */
type SortKey =
  | { k: 0 } // null
  | { k: 1; n: number }
  | { k: 2; s: string }

const NUM_RE = /^-?\d+(\.\d+)?([eE][+-]?\d+)?$/

export function toQuerySortKey(v: unknown): SortKey {
  if (isQueryNullValue(v)) return { k: 0 }
  if (typeof v === 'number') {
    if (Number.isNaN(v)) return { k: 0 }
    return { k: 1, n: v }
  }
  if (typeof v === 'bigint') {
    const n = Number(v)
    if (Number.isFinite(n)) return { k: 1, n }
    return { k: 2, s: String(v) }
  }
  if (typeof v === 'boolean') return { k: 1, n: v ? 1 : 0 }
  if (v instanceof Date) {
    const t = v.getTime()
    if (Number.isNaN(t)) return { k: 0 }
    return { k: 1, n: t }
  }

  const sa = String(v).trim()
  if (NUM_RE.test(sa)) {
    const n = Number(sa)
    if (Number.isFinite(n)) return { k: 1, n }
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(sa)) {
    const t = Date.parse(sa)
    if (!Number.isNaN(t)) return { k: 1, n: t }
  }
  return { k: 2, s: sa }
}

export function compareQuerySortKeys(a: SortKey, b: SortKey): number {
  if (a.k !== b.k) {
    // null(0) 升序沉底 → 排在 number/string 之后
    if (a.k === 0) return 1
    if (b.k === 0) return -1
    return a.k - b.k
  }
  if (a.k === 0) return 0
  if (a.k === 1 && b.k === 1) return a.n === b.n ? 0 : a.n < b.n ? -1 : 1
  if (a.k === 2 && b.k === 2) {
    return a.s < b.s ? -1 : a.s > b.s ? 1 : 0
  }
  return 0
}

export function compareQueryCellValues(a: unknown, b: unknown): number {
  return compareQuerySortKeys(toQuerySortKey(a), toQuerySortKey(b))
}

/**
 * 全量排序。用下标数组排序，只在最后映射回行，减少对象搬迁。
 * n≤1 或无需排序时返回原引用，避免无谓拷贝。
 */
export function sortQueryRows<T extends Record<string, unknown>>(
  rows: T[],
  field: string,
  order: QuerySortOrder,
): T[] {
  const n = rows.length
  if (n <= 1) return rows

  const keys = new Array<SortKey>(n)
  for (let i = 0; i < n; i++) keys[i] = toQuerySortKey(rows[i][field])

  const idx = new Array<number>(n)
  for (let i = 0; i < n; i++) idx[i] = i

  const dir = order === 'ascend' ? 1 : -1
  idx.sort((i, j) => dir * compareQuerySortKeys(keys[i], keys[j]))

  const out = new Array<T>(n)
  for (let i = 0; i < n; i++) out[i] = rows[idx[i]]
  return out
}

/**
 * 结果集内容指纹：用于判断是否「新一次查询」。
 * 避免父组件每次 render 新建 dataSource 数组引用导致排序状态被清空。
 */
export function queryResultDataFingerprint(rows: Array<Record<string, unknown>>): string {
  const n = rows.length
  if (!n) return '0'
  const cols = Object.keys(rows[0]).filter(k => k !== '_key')
  const colSig = cols.join('\x1e')
  let h = (n * 2654435761) >>> 0
  const pick = n <= 12
    ? Array.from({ length: n }, (_, i) => i)
    : [0, 1, 2, Math.floor(n / 2) - 1, Math.floor(n / 2), Math.floor(n / 2) + 1, n - 3, n - 2, n - 1]
  for (const i of pick) {
    if (i < 0 || i >= n) continue
    const r = rows[i]
    for (let c = 0; c < Math.min(cols.length, 6); c++) {
      const s = String(r[cols[c]] ?? '')
      for (let p = 0; p < Math.min(s.length, 24); p++) {
        h = (Math.imul(h ^ s.charCodeAt(p), 16777619)) >>> 0
      }
      h = (h ^ (s.length * 31)) >>> 0
    }
  }
  return `${n}:${colSig}:${h}`
}

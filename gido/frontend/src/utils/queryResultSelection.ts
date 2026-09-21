/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Rectangular cell selection for query result grids (DataGrip / spreadsheet style).
 */
import { formatCellDisplay } from './cellDisplay'

export type QueryResultSelectionAnchor = {
  row: number
  col: number
}

export type QueryResultSelectionRange = {
  startRow: number
  endRow: number
  startCol: number
  endCol: number
}

export type QueryResultSelectionAggregates = {
  cells: number
  nulls: number
  numericCount: number
  sum: number | null
  avg: number | null
  min: number | null
  max: number | null
}

export function normalizeQueryResultSelection(
  a: QueryResultSelectionAnchor,
  b: QueryResultSelectionAnchor,
): QueryResultSelectionRange {
  return {
    startRow: Math.min(a.row, b.row),
    endRow: Math.max(a.row, b.row),
    startCol: Math.min(a.col, b.col),
    endCol: Math.max(a.col, b.col),
  }
}

export function isQueryResultCellSelected(
  range: QueryResultSelectionRange | null | undefined,
  row: number,
  col: number,
): boolean {
  if (!range) return false
  return (
    row >= range.startRow
    && row <= range.endRow
    && col >= range.startCol
    && col <= range.endCol
  )
}

export function formatQueryResultSelectionTsv(
  rows: Array<Record<string, unknown>>,
  leafKeys: string[],
  range: QueryResultSelectionRange,
  opts?: { includeHeader?: boolean },
): string {
  const keys = leafKeys.slice(range.startCol, range.endCol + 1)
  const body = rows
    .slice(range.startRow, range.endRow + 1)
    .map(record => keys.map(key => {
      const value = record[key]
      if (value === null || value === undefined) return 'NULL'
      return formatCellDisplay(value, 0)
    }).join('\t'))
    .join('\n')
  if (!opts?.includeHeader) return body
  return `${keys.join('\t')}\n${body}`
}

export function queryResultSelectionSummary(range: QueryResultSelectionRange): string {
  const rows = range.endRow - range.startRow + 1
  const cols = range.endCol - range.startCol + 1
  if (rows === 1 && cols === 1) return '1 个单元格'
  return `${rows} × ${cols} 单元格`
}

/** Focus end of a range (corner opposite the anchor when possible). */
export function selectionFocusCell(
  range: QueryResultSelectionRange,
  anchor: QueryResultSelectionAnchor | null,
): QueryResultSelectionAnchor {
  if (!anchor) return { row: range.endRow, col: range.endCol }
  if (anchor.row === range.startRow && anchor.col === range.startCol) {
    return { row: range.endRow, col: range.endCol }
  }
  if (anchor.row === range.endRow && anchor.col === range.endCol) {
    return { row: range.startRow, col: range.startCol }
  }
  if (anchor.row === range.startRow && anchor.col === range.endCol) {
    return { row: range.endRow, col: range.startCol }
  }
  if (anchor.row === range.endRow && anchor.col === range.startCol) {
    return { row: range.startRow, col: range.endCol }
  }
  return { row: range.endRow, col: range.endCol }
}

export function moveQueryResultFocus(
  focus: QueryResultSelectionAnchor,
  key: string,
  bounds: { rows: number; cols: number },
  opts?: { jump?: boolean },
): QueryResultSelectionAnchor | null {
  if (bounds.rows < 1 || bounds.cols < 1) return null
  let { row, col } = focus
  const jump = Boolean(opts?.jump)
  switch (key) {
    case 'ArrowUp':
      row = jump ? 0 : row - 1
      break
    case 'ArrowDown':
      row = jump ? bounds.rows - 1 : row + 1
      break
    case 'ArrowLeft':
      col = jump ? 0 : col - 1
      break
    case 'ArrowRight':
      col = jump ? bounds.cols - 1 : col + 1
      break
    case 'Home':
      col = 0
      if (jump) row = 0
      break
    case 'End':
      col = bounds.cols - 1
      if (jump) row = bounds.rows - 1
      break
    case 'PageUp':
      row -= jump ? bounds.rows : 10
      break
    case 'PageDown':
      row += jump ? bounds.rows : 10
      break
    default:
      return null
  }
  return {
    row: Math.max(0, Math.min(bounds.rows - 1, row)),
    col: Math.max(0, Math.min(bounds.cols - 1, col)),
  }
}

export type QueryResultFindMatch = QueryResultSelectionAnchor

export function findQueryResultMatches(
  rows: Array<Record<string, unknown>>,
  leafKeys: string[],
  query: string,
): QueryResultFindMatch[] {
  const needle = query.trim().toLowerCase()
  if (!needle) return []
  const matches: QueryResultFindMatch[] = []
  for (let row = 0; row < rows.length; row += 1) {
    const record = rows[row]
    if (!record) continue
    for (let col = 0; col < leafKeys.length; col += 1) {
      const value = record[leafKeys[col]]
      const text = value === null || value === undefined
        ? 'null'
        : formatCellDisplay(value, 0).toLowerCase()
      if (text.includes(needle)) matches.push({ row, col })
    }
  }
  return matches
}

export function selectQueryResultRow(
  row: number,
  cols: number,
): QueryResultSelectionRange | null {
  if (row < 0 || cols < 1) return null
  return { startRow: row, endRow: row, startCol: 0, endCol: cols - 1 }
}

export function selectQueryResultColumn(
  col: number,
  rows: number,
): QueryResultSelectionRange | null {
  if (col < 0 || rows < 1) return null
  return { startRow: 0, endRow: rows - 1, startCol: col, endCol: col }
}

export function selectQueryResultAll(
  rows: number,
  cols: number,
): QueryResultSelectionRange | null {
  if (rows < 1 || cols < 1) return null
  return { startRow: 0, endRow: rows - 1, startCol: 0, endCol: cols - 1 }
}

export type QueryColumnPageStats = {
  column: string
  cells: number
  nulls: number
  distinct: number
  numericCount: number
  sum: number | null
  min: number | null
  max: number | null
}

export function computeQueryColumnPageStats(
  rows: Array<Record<string, unknown>>,
  column: string,
): QueryColumnPageStats {
  let nulls = 0
  const distinct = new Set<string>()
  const nums: number[] = []
  for (const record of rows) {
    const value = record[column]
    if (value === null || value === undefined) {
      nulls += 1
      distinct.add('__null__')
      continue
    }
    distinct.add(formatCellDisplay(value, 0))
    const n = asFiniteNumber(value)
    if (n != null) nums.push(n)
  }
  const cells = rows.length
  if (!nums.length) {
    return {
      column,
      cells,
      nulls,
      distinct: distinct.size,
      numericCount: 0,
      sum: null,
      min: null,
      max: null,
    }
  }
  const sum = nums.reduce((acc, n) => acc + n, 0)
  return {
    column,
    cells,
    nulls,
    distinct: distinct.size,
    numericCount: nums.length,
    sum,
    min: Math.min(...nums),
    max: Math.max(...nums),
  }
}

function asFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'bigint') {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  if (typeof value === 'string' && value.trim() && !/^0\d/.test(value.trim())) {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  return null
}

export function computeQueryResultSelectionAggregates(
  rows: Array<Record<string, unknown>>,
  leafKeys: string[],
  range: QueryResultSelectionRange,
): QueryResultSelectionAggregates {
  const keys = leafKeys.slice(range.startCol, range.endCol + 1)
  let cells = 0
  let nulls = 0
  const nums: number[] = []
  for (let r = range.startRow; r <= range.endRow; r += 1) {
    const record = rows[r]
    if (!record) continue
    for (const key of keys) {
      cells += 1
      const value = record[key]
      if (value === null || value === undefined) {
        nulls += 1
        continue
      }
      const n = asFiniteNumber(value)
      if (n != null) nums.push(n)
    }
  }
  if (!nums.length) {
    return {
      cells,
      nulls,
      numericCount: 0,
      sum: null,
      avg: null,
      min: null,
      max: null,
    }
  }
  const sum = nums.reduce((acc, n) => acc + n, 0)
  return {
    cells,
    nulls,
    numericCount: nums.length,
    sum,
    avg: sum / nums.length,
    min: Math.min(...nums),
    max: Math.max(...nums),
  }
}

export function formatQueryResultAggregateNumber(value: number): string {
  if (Number.isInteger(value)) return String(value)
  const abs = Math.abs(value)
  if (abs >= 1e6 || (abs > 0 && abs < 1e-4)) return value.toExponential(4)
  return String(Number(value.toPrecision(8)))
}

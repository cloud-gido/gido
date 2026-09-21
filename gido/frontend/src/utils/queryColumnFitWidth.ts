/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * DataGrip / Excel-style column auto-fit:
 * - Header expand: full field names stay readable (H-scroll OK).
 * - Viewport fit: headers stay readable; cell-driven growth is capped so the
 *   grid stays denser, then spare width stretches upward (never crush headers).
 */
import { formatCellDisplay } from './cellDisplay'

export const QUERY_COLUMN_FIT_MIN = 72
export const QUERY_COLUMN_FIT_MAX = 480
/** Cap how many rows we sample so fit stays cheap on wide pages. */
export const QUERY_COLUMN_FIT_SAMPLE = 200
/** Row-number gutter reserved when fitting to the viewport. */
export const QUERY_RESULT_ROWNUM_WIDTH = 44
/**
 * Chrome inside each header cell: type badge, antd sorter, filter trigger,
 * resize handle, and padding. Under-counting this is why names become `st…`.
 */
export const QUERY_COLUMN_HEADER_CHROME = 128
/** Cell text padding when measuring sample values. */
const CELL_PAD = 28
/** Viewport-fit caps how wide cells alone may push a column (headers still win). */
export const QUERY_COLUMN_VIEWPORT_CELL_CAP = 168

let canvasCtx: CanvasRenderingContext2D | null | undefined

function measureCtx(): CanvasRenderingContext2D | null {
  if (canvasCtx !== undefined) return canvasCtx
  if (typeof document === 'undefined') {
    canvasCtx = null
    return null
  }
  const canvas = document.createElement('canvas')
  canvasCtx = canvas.getContext('2d')
  return canvasCtx
}

export function measureTextWidthPx(
  text: string,
  font = '12px ui-monospace, SFMono-Regular, Menlo, monospace',
): number {
  const ctx = measureCtx()
  if (!ctx) {
    // jsdom / SSR fallback: rough monospace estimate
    return Math.ceil(text.length * 7.2)
  }
  ctx.font = font
  return ctx.measureText(text).width
}

export function measureQueryHeaderWidth(opts: {
  column: string
  header?: string
  min?: number
  max?: number
}): number {
  const min = opts.min ?? QUERY_COLUMN_FIT_MIN
  const max = opts.max ?? 720
  const header = opts.header ?? opts.column
  const headerFont = '600 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
  const width = measureTextWidthPx(header, headerFont) + QUERY_COLUMN_HEADER_CHROME
  return Math.max(min, Math.min(max, Math.ceil(width)))
}

export function measureQueryColumnWidth(opts: {
  column: string
  header?: string
  values: unknown[]
  min?: number
  max?: number
  sample?: number
  /** When set, sample cells cannot push the column wider than this (header still can). */
  cellCap?: number
}): number {
  const min = opts.min ?? QUERY_COLUMN_FIT_MIN
  const max = opts.max ?? QUERY_COLUMN_FIT_MAX
  const sample = opts.sample ?? QUERY_COLUMN_FIT_SAMPLE
  const headerWidth = measureQueryHeaderWidth({
    column: opts.column,
    header: opts.header,
    min,
    max,
  })
  let cellWidth = 0
  const rows = opts.values.length > sample ? opts.values.slice(0, sample) : opts.values
  for (const value of rows) {
    const label = value === null || value === undefined ? 'NULL' : formatCellDisplay(value, 0)
    const clipped = label.length > 80 ? `${label.slice(0, 80)}…` : label
    cellWidth = Math.max(cellWidth, measureTextWidthPx(clipped) + CELL_PAD)
  }
  if (opts.cellCap != null) cellWidth = Math.min(cellWidth, opts.cellCap)
  return Math.max(min, Math.min(max, Math.ceil(Math.max(headerWidth, cellWidth))))
}

export type QueryColumnFitMode = 'content' | 'viewport'

/**
 * Expand so every field name is readable. Cells may widen further.
 * Wide schemas keep a horizontal scrollbar.
 */
export function fitQueryColumnsToContent(opts: {
  columns: string[]
  rows: Array<Record<string, unknown>>
  max?: number
}): Record<string, number> {
  const max = opts.max ?? 720
  return Object.fromEntries(opts.columns.map(column => [
    column,
    measureQueryColumnWidth({
      column,
      values: opts.rows.map(row => row[column]),
      max,
    }),
  ]))
}

/**
 * Denser “fit window”: headers stay fully readable, cell growth is capped,
 * then spare viewport width stretches columns. Never shrinks below header width.
 */
export function fitQueryColumnsToViewport(opts: {
  columns: string[]
  rows: Array<Record<string, unknown>>
  viewportWidth: number
  reservedWidth?: number
  /** @deprecated Ignored — fit always remeasures so crushed layouts recover. */
  currentWidths?: Record<string, number>
}): Record<string, number> {
  const reserved = opts.reservedWidth ?? QUERY_RESULT_ROWNUM_WIDTH
  const available = Math.max(
    QUERY_COLUMN_FIT_MIN * Math.max(opts.columns.length, 1),
    Math.floor(opts.viewportWidth - reserved - 8),
  )
  const fitted = opts.columns.map(column => ({
    column,
    width: measureQueryColumnWidth({
      column,
      values: opts.rows.map(row => row[column]),
      max: 720,
      cellCap: QUERY_COLUMN_VIEWPORT_CELL_CAP,
    }),
  }))
  const total = fitted.reduce((sum, item) => sum + item.width, 0)
  if (total <= 0) {
    const even = Math.max(QUERY_COLUMN_FIT_MIN, Math.floor(available / Math.max(opts.columns.length, 1)))
    return Object.fromEntries(opts.columns.map(column => [column, even]))
  }
  if (total >= available) {
    return Object.fromEntries(fitted.map(item => [item.column, item.width]))
  }
  const scale = available / total
  const scaled = fitted.map(item => ({
    column: item.column,
    width: Math.max(item.width, Math.floor(item.width * scale)),
  }))
  let used = scaled.reduce((sum, item) => sum + item.width, 0)
  let i = 0
  while (used < available && scaled.length) {
    scaled[i % scaled.length].width += 1
    used += 1
    i += 1
  }
  return Object.fromEntries(scaled.map(item => [item.column, item.width]))
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * DataGrip / Excel-style column auto-fit:
 * - Content fit expands to header + sample cells (headers stay readable).
 * - Viewport fit only stretches when there is spare room; never crush columns.
 */
import { formatCellDisplay } from './cellDisplay'

export const QUERY_COLUMN_FIT_MIN = 56
export const QUERY_COLUMN_FIT_MAX = 480
/** Cap how many rows we sample so fit stays cheap on wide pages. */
export const QUERY_COLUMN_FIT_SAMPLE = 200
/** Row-number gutter reserved when fitting to the viewport. */
export const QUERY_RESULT_ROWNUM_WIDTH = 44
/** Extra room for type / sort / filter icons beside the header label. */
const HEADER_PAD = 72
const CELL_PAD = 28

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

export function measureQueryColumnWidth(opts: {
  column: string
  header?: string
  values: unknown[]
  min?: number
  max?: number
  sample?: number
}): number {
  const min = opts.min ?? QUERY_COLUMN_FIT_MIN
  const max = opts.max ?? QUERY_COLUMN_FIT_MAX
  const sample = opts.sample ?? QUERY_COLUMN_FIT_SAMPLE
  const header = opts.header ?? opts.column
  const headerFont = '600 12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
  let width = measureTextWidthPx(header, headerFont) + HEADER_PAD

  const rows = opts.values.length > sample ? opts.values.slice(0, sample) : opts.values
  for (const value of rows) {
    const label = value === null || value === undefined ? 'NULL' : formatCellDisplay(value, 0)
    const clipped = label.length > 80 ? `${label.slice(0, 80)}…` : label
    width = Math.max(width, measureTextWidthPx(clipped) + CELL_PAD)
  }
  return Math.max(min, Math.min(max, Math.ceil(width)))
}

export type QueryColumnFitMode = 'content' | 'viewport'

/**
 * Size each column from header + sample cell text. Wide schemas keep a
 * horizontal scrollbar instead of shrinking labels out of sight.
 */
export function fitQueryColumnsToContent(opts: {
  columns: string[]
  rows: Array<Record<string, unknown>>
  max?: number
}): Record<string, number> {
  const max = opts.max ?? 1200
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
 * Content-fit first, then stretch upward to fill spare viewport width.
 * Never scales columns below their content width (DataGrip / Excel autofit).
 */
export function fitQueryColumnsToViewport(opts: {
  columns: string[]
  rows: Array<Record<string, unknown>>
  viewportWidth: number
  reservedWidth?: number
  /** @deprecated Ignored — fit always remeasures from content so crushed layouts recover. */
  currentWidths?: Record<string, number>
}): Record<string, number> {
  const reserved = opts.reservedWidth ?? QUERY_RESULT_ROWNUM_WIDTH
  const available = Math.max(
    QUERY_COLUMN_FIT_MIN * Math.max(opts.columns.length, 1),
    Math.floor(opts.viewportWidth - reserved - 8),
  )
  const contentWidths = fitQueryColumnsToContent({
    columns: opts.columns,
    rows: opts.rows,
    max: 1200,
  })
  const fitted = opts.columns.map(column => ({
    column,
    width: contentWidths[column] ?? QUERY_COLUMN_FIT_MIN,
  }))
  const total = fitted.reduce((sum, item) => sum + item.width, 0)
  if (total <= 0) {
    const even = Math.max(QUERY_COLUMN_FIT_MIN, Math.floor(available / Math.max(opts.columns.length, 1)))
    return Object.fromEntries(opts.columns.map(column => [column, even]))
  }
  // Wider than the window: keep content widths and allow horizontal scroll.
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

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  fitQueryColumnsToViewport,
  measureQueryColumnWidth,
  measureTextWidthPx,
} from './queryColumnFitWidth'
import {
  computeQueryColumnPageStats,
  computeQueryResultSelectionAggregates,
  findQueryResultMatches,
  formatQueryResultAggregateNumber,
  formatQueryResultSelectionTsv,
  isQueryResultCellSelected,
  moveQueryResultFocus,
  normalizeQueryResultSelection,
  queryResultSelectionSummary,
  selectQueryResultAll,
  selectQueryResultColumn,
  selectQueryResultRow,
  selectionFocusCell,
  shouldUseQueryResultVirtual,
} from './queryResultSelection'

describe('queryColumnFitWidth', () => {
  it('grows with longer header and cell values and stays within bounds', () => {
    const narrow = measureQueryColumnWidth({
      column: 'id',
      values: [1, 2, 3],
    })
    const wide = measureQueryColumnWidth({
      column: 'very_long_metric_column_name',
      values: ['short', 'a'.repeat(60)],
    })
    expect(wide).toBeGreaterThan(narrow)
    expect(narrow).toBeGreaterThanOrEqual(56)
    expect(wide).toBeLessThanOrEqual(480)
    expect(measureTextWidthPx('abc')).toBeGreaterThan(0)
  })

  it('stretches fitted columns to fill a wide viewport', () => {
    const widths = fitQueryColumnsToViewport({
      columns: ['a', 'b'],
      rows: [{ a: 1, b: 2 }],
      viewportWidth: 800,
      reservedWidth: 44,
    })
    expect(widths.a + widths.b).toBeGreaterThanOrEqual(800 - 44 - 8 - 2)
    expect(widths.a).toBeGreaterThanOrEqual(56)
    expect(widths.b).toBeGreaterThanOrEqual(56)
  })
})

describe('queryResultSelection', () => {
  it('normalizes anchors into an inclusive rectangle', () => {
    const range = normalizeQueryResultSelection(
      { row: 4, col: 2 },
      { row: 1, col: 5 },
    )
    expect(range).toEqual({ startRow: 1, endRow: 4, startCol: 2, endCol: 5 })
    expect(isQueryResultCellSelected(range, 2, 3)).toBe(true)
    expect(isQueryResultCellSelected(range, 0, 3)).toBe(false)
    expect(queryResultSelectionSummary(range)).toBe('4 × 4 单元格')
  })

  it('serializes the selected rectangle as TSV with optional header', () => {
    const rows = [
      { a: 1, b: 'x', c: null },
      { a: 2, b: 'y', c: 'z' },
      { a: 3, b: 'w', c: 'v' },
    ]
    const range = normalizeQueryResultSelection({ row: 0, col: 1 }, { row: 1, col: 2 })
    expect(formatQueryResultSelectionTsv(rows, ['a', 'b', 'c'], range)).toBe('x\tNULL\ny\tz')
    expect(formatQueryResultSelectionTsv(rows, ['a', 'b', 'c'], range, { includeHeader: true }))
      .toBe('b\tc\nx\tNULL\ny\tz')
  })

  it('moves focus with arrows, jumps with meta, and finds matches', () => {
    expect(moveQueryResultFocus({ row: 1, col: 1 }, 'ArrowLeft', { rows: 3, cols: 3 }))
      .toEqual({ row: 1, col: 0 })
    expect(moveQueryResultFocus({ row: 1, col: 1 }, 'ArrowUp', { rows: 5, cols: 3 }, { jump: true }))
      .toEqual({ row: 0, col: 1 })
    expect(moveQueryResultFocus({ row: 1, col: 1 }, 'End', { rows: 5, cols: 4 }, { jump: true }))
      .toEqual({ row: 4, col: 3 })
    expect(moveQueryResultFocus({ row: 0, col: 0 }, 'Home', { rows: 3, cols: 4 }))
      .toEqual({ row: 0, col: 0 })
    const range = normalizeQueryResultSelection({ row: 0, col: 0 }, { row: 1, col: 1 })
    expect(selectionFocusCell(range, { row: 0, col: 0 })).toEqual({ row: 1, col: 1 })
    const aggregates = computeQueryResultSelectionAggregates(
      [
        { a: 1, b: null },
        { a: 3, b: 5 },
      ],
      ['a', 'b'],
      range,
    )
    expect(aggregates.cells).toBe(4)
    expect(aggregates.nulls).toBe(1)
    expect(aggregates.numericCount).toBe(3)
    expect(aggregates.sum).toBe(9)
    expect(aggregates.avg).toBe(3)
    expect(aggregates.min).toBe(1)
    expect(aggregates.max).toBe(5)
    expect(formatQueryResultAggregateNumber(3.1415926535)).toContain('3.1415927')
    expect(findQueryResultMatches(
      [{ name: 'Alice', city: 'Paris' }, { name: 'Bob', city: 'paris' }],
      ['name', 'city'],
      'par',
    )).toEqual([{ row: 0, col: 1 }, { row: 1, col: 1 }])
  })

  it('selects full rows/columns/page and computes focus-column page stats', () => {
    expect(selectQueryResultRow(2, 4)).toEqual({
      startRow: 2, endRow: 2, startCol: 0, endCol: 3,
    })
    expect(selectQueryResultColumn(1, 5)).toEqual({
      startRow: 0, endRow: 4, startCol: 1, endCol: 1,
    })
    expect(selectQueryResultAll(3, 2)).toEqual({
      startRow: 0, endRow: 2, startCol: 0, endCol: 1,
    })
    expect(shouldUseQueryResultVirtual(101, 10)).toBe(true)
    expect(shouldUseQueryResultVirtual(50, 10)).toBe(false)
    expect(shouldUseQueryResultVirtual(200, 40)).toBe(false)
    const stats = computeQueryColumnPageStats(
      [{ a: 1 }, { a: null }, { a: 1 }, { a: 4 }],
      'a',
    )
    expect(stats.cells).toBe(4)
    expect(stats.nulls).toBe(1)
    expect(stats.distinct).toBe(3)
    expect(stats.numericCount).toBe(3)
    expect(stats.sum).toBe(6)
    expect(stats.min).toBe(1)
    expect(stats.max).toBe(4)
  })
})

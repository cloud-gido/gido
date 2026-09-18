/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  PROBE_DEFAULT_ROW_LIMIT,
  SQL_RESULT_ROW_CAP,
  clampSqlResultRowLimit,
  formatSqlResultRowLimitShort,
  sqlRunWithRowLimitLabel,
} from './sqlResultRowLimit'

describe('sqlResultRowLimit', () => {
  it('shares 10000 cap with studio / probe', () => {
    expect(SQL_RESULT_ROW_CAP).toBe(10000)
    expect(PROBE_DEFAULT_ROW_LIMIT).toBe(10000)
  })

  it('clamps invalid and out-of-range values', () => {
    expect(clampSqlResultRowLimit(undefined)).toBe(10000)
    expect(clampSqlResultRowLimit(0)).toBe(1)
    expect(clampSqlResultRowLimit(50_000)).toBe(10000)
    expect(clampSqlResultRowLimit(138)).toBe(138)
  })

  it('formats run button short labels', () => {
    expect(formatSqlResultRowLimitShort(10000)).toBe('1万')
    expect(formatSqlResultRowLimitShort(1000)).toBe('1千')
    expect(formatSqlResultRowLimitShort(500)).toBe('500')
    expect(sqlRunWithRowLimitLabel(10000)).toBe('运行 · 1万')
    expect(sqlRunWithRowLimitLabel(100, true)).toBe('运行中...')
  })
})

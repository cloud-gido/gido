/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  PROBE_DEFAULT_ROW_LIMIT,
  SQL_RESULT_ROW_CAP,
  clampSqlResultRowLimit,
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
})

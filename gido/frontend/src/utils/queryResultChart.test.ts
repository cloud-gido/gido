/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  buildQueryChartPoints,
  isCategoryChartField,
  isNumericChartField,
  suggestQueryChartAxes,
} from './queryResultChart'

describe('queryResultChart', () => {
  it('suggests category and numeric axes', () => {
    expect(suggestQueryChartAxes([
      { name: 'city', semantic_type: 'string' },
      { name: 'amount', semantic_type: 'number' },
    ])).toEqual({ category: 'city', value: 'amount' })
  })

  it('classifies numeric and category fields', () => {
    expect(isNumericChartField({ name: 'n', type: 'bigint' })).toBe(true)
    expect(isCategoryChartField({ name: 'name', type: 'varchar' })).toBe(true)
    expect(isCategoryChartField({ name: 'n', semantic_type: 'number' })).toBe(false)
  })

  it('aggregates viewport rows by category', () => {
    const points = buildQueryChartPoints([
      { city: 'a', amount: 1 },
      { city: 'a', amount: 2 },
      { city: 'b', amount: 4 },
      { city: null, amount: 3 },
    ], 'city', 'amount')
    expect(points).toEqual([
      { category: 'b', value: 4 },
      { category: 'a', value: 3 },
      { category: '(空)', value: 3 },
    ])
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { buildExplainPlanTree, parseExplainPlanMetrics } from './explainPlanTree'

describe('parseExplainPlanMetrics', () => {
  it('extracts cost and rows from postgres-style lines', () => {
    expect(parseExplainPlanMetrics('Seq Scan on t  (cost=0.00..35.50 rows=2550 width=4)')).toEqual({
      cost: '0.00..35.50',
      rows: '2550',
    })
  })

  it('returns empty object when metrics are absent', () => {
    expect(parseExplainPlanMetrics('HASH JOIN')).toEqual({})
  })
})

describe('buildExplainPlanTree', () => {
  it('attaches metrics onto tree nodes', () => {
    const tree = buildExplainPlanTree({
      rows: [
        ['-> Hash Join  (cost=10.00..20.00 rows=100)'],
        ['  -> Seq Scan on a  (cost=0.00..5.00 rows=50)'],
      ],
    })
    expect(tree[0].metrics).toEqual({ cost: '10.00..20.00', rows: '100' })
    expect(tree[0].children?.[0].metrics).toEqual({ cost: '0.00..5.00', rows: '50' })
  })
})

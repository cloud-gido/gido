/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  columnKeysSignature,
  mergeColumnOrderWithKeys,
  pruneWidths,
  resolveResultColumnOrder,
} from './resultTableMeta'
import {
  compareQueryCellValues,
  queryResultDataFingerprint,
  sortQueryRows,
  toQuerySortKey,
} from './queryCellSort'

describe('resultTableMeta', () => {
  it('mergeColumnOrderWithKeys prefers saved order then appends new keys', () => {
    expect(mergeColumnOrderWithKeys(['c', 'a'], ['a', 'b', 'c'])).toEqual(['c', 'a', 'b'])
  })

  it('resolveResultColumnOrder follows SQL when sourceKeys missing (legacy cache)', () => {
    expect(resolveResultColumnOrder(['a', 'b', 'c'], ['c', 'b', 'a'])).toEqual(['c', 'b', 'a'])
  })

  it('resolveResultColumnOrder keeps drag order when sourceKeys matches current keys', () => {
    expect(
      resolveResultColumnOrder(['b', 'a', 'c'], ['a', 'b', 'c'], ['a', 'b', 'c']),
    ).toEqual(['b', 'a', 'c'])
  })

  it('resolveResultColumnOrder resets when SELECT column order changes', () => {
    expect(
      resolveResultColumnOrder(['a', 'b', 'c'], ['c', 'b', 'a'], ['a', 'b', 'c']),
    ).toEqual(['c', 'b', 'a'])
  })

  it('columnKeysSignature is order-sensitive', () => {
    expect(columnKeysSignature(['a', 'b'])).not.toEqual(columnKeysSignature(['b', 'a']))
  })

  it('pruneWidths drops unknown columns', () => {
    expect(pruneWidths({ a: 10, z: 20 }, ['a', 'b'])).toEqual({ a: 10 })
  })
})

describe('queryCellSort', () => {
  it('sorts numbers ascending with nulls last', () => {
    const rows = [
      { _key: 0, v: 3 },
      { _key: 1, v: null },
      { _key: 2, v: 1 },
      { _key: 3, v: 'None' },
      { _key: 4, v: 2 },
    ]
    const asc = sortQueryRows(rows, 'v', 'ascend').map(r => r.v)
    expect(asc).toEqual([1, 2, 3, null, 'None'])
  })

  it('descending is reverse of ascending for mixed values', () => {
    const rows = [
      { _key: 0, v: 'b' },
      { _key: 1, v: 'a' },
      { _key: 2, v: 'c' },
    ]
    const asc = sortQueryRows(rows, 'v', 'ascend').map(r => r.v)
    const desc = sortQueryRows(rows, 'v', 'descend').map(r => r.v)
    expect(asc).toEqual(['a', 'b', 'c'])
    expect(desc).toEqual(['c', 'b', 'a'])
  })

  it('parses numeric and date strings', () => {
    expect(toQuerySortKey('10').k).toBe(1)
    expect(toQuerySortKey('2026-07-30 08:00:00').k).toBe(1)
    expect(compareQueryCellValues('2', '10')).toBeLessThan(0)
    expect(compareQueryCellValues('2026-01-02', '2026-01-01')).toBeGreaterThan(0)
  })

  it('fingerprint stable for same content different array identity', () => {
    const a = [
      { _key: 0, x: 1, y: 'a' },
      { _key: 1, x: 2, y: 'b' },
    ]
    const b = a.map(r => ({ ...r }))
    expect(queryResultDataFingerprint(a)).toEqual(queryResultDataFingerprint(b))
  })

  it('fingerprint changes when values change', () => {
    const a = [{ _key: 0, x: 1 }]
    const b = [{ _key: 0, x: 2 }]
    expect(queryResultDataFingerprint(a)).not.toEqual(queryResultDataFingerprint(b))
  })

  it('sorts 10k rows without throwing (smoke)', () => {
    const rows = Array.from({ length: 10_000 }, (_, i) => ({
      _key: i,
      v: (i * 37) % 997,
    }))
    const t0 = Date.now()
    const out = sortQueryRows(rows, 'v', 'ascend')
    const ms = Date.now() - t0
    expect(out).toHaveLength(10_000)
    for (let i = 1; i < out.length; i++) {
      expect(Number(out[i].v)).toBeGreaterThanOrEqual(Number(out[i - 1].v))
    }
    // CI / 慢机器放宽；本地通常远小于此
    expect(ms).toBeLessThan(2000)
  })
})

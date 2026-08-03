/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { sortByName } from './treeSort'

describe('treeSort', () => {
  it('按中文名称字典序，忽略 sort_order', () => {
    const sorted = sortByName([
      { id: 3, name: 'zeta', sort_order: 10 },
      { id: 1, name: 'alpha', sort_order: 30 },
      { id: 2, name: 'beta', sort_order: 20 },
    ] as { id: number; name: string; sort_order?: number }[])
    expect(sorted.map(x => x.name)).toEqual(['alpha', 'beta', 'zeta'])
  })

  it('同名时按 id 稳定排序（number / string）', () => {
    expect(
      sortByName([
        { id: 20, name: 'a' },
        { id: 10, name: 'a' },
      ]).map(x => x.id),
    ).toEqual([10, 20])
    expect(
      sortByName([
        { id: 's-b', name: 'a' },
        { id: 's-a', name: 'a' },
      ]).map(x => x.id),
    ).toEqual(['s-a', 's-b'])
  })
})

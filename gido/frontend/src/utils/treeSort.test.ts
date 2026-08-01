/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { sortFoldersByName, sortLeavesByOrderThenName } from './treeSort'

describe('treeSort', () => {
  it('sort_order=0 时按中文名称字典序', () => {
    const sorted = sortLeavesByOrderThenName([
      { id: 3, name: 'zeta', sort_order: 0 },
      { id: 1, name: 'alpha', sort_order: 0 },
      { id: 2, name: 'beta', sort_order: 0 },
    ])
    expect(sorted.map(x => x.name)).toEqual(['alpha', 'beta', 'zeta'])
  })

  it('手工 sort_order 优先于名称', () => {
    const sorted = sortLeavesByOrderThenName([
      { id: 1, name: 'alpha', sort_order: 30 },
      { id: 2, name: 'beta', sort_order: 10 },
      { id: 3, name: 'gamma', sort_order: 20 },
    ])
    expect(sorted.map(x => x.id)).toEqual([2, 3, 1])
  })

  it('同序同名时按 id 稳定排序（number / string）', () => {
    expect(
      sortLeavesByOrderThenName([
        { id: 20, name: 'a', sort_order: 0 },
        { id: 10, name: 'a', sort_order: 0 },
      ]).map(x => x.id),
    ).toEqual([10, 20])
    expect(
      sortLeavesByOrderThenName([
        { id: 's-b', name: 'a', sort_order: 0 },
        { id: 's-a', name: 'a', sort_order: 0 },
      ]).map(x => x.id),
    ).toEqual(['s-a', 's-b'])
  })

  it('sortFoldersByName 仅按名称', () => {
    const sorted = sortFoldersByName([
      { id: 2, name: '目录B' },
      { id: 1, name: '目录A' },
    ])
    expect(sorted.map(x => x.id)).toEqual([1, 2])
  })
})

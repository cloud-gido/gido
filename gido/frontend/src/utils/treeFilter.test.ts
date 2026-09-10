/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { filterWorkspaceTree } from './treeFilter'

const folders = [
  { id: 1, name: '个人目录', parent_id: null as number | null },
  { id: 2, name: 'dwd', parent_id: 1 },
  { id: 3, name: 'ads', parent_id: 1 },
]

const leaves = [
  { id: 10, name: 'dwd_order', folder_id: 2 },
  { id: 11, name: 'ads_user', folder_id: 3 },
  { id: 12, name: 'root_script', folder_id: null as number | null },
]

describe('filterWorkspaceTree', () => {
  it('empty query returns all', () => {
    const out = filterWorkspaceTree({ folders, leaves, query: '  ' })
    expect(out.folders).toEqual(folders)
    expect(out.leaves).toEqual(leaves)
    expect(out.expandFolderKeys).toEqual([])
  })

  it('keeps ancestors when leaf matches', () => {
    const out = filterWorkspaceTree({ folders, leaves, query: 'order' })
    expect(out.leaves.map(l => l.id)).toEqual([10])
    expect(out.folders.map(f => f.id).sort()).toEqual([1, 2])
    expect(out.expandFolderKeys).toContain('folder-1')
    expect(out.expandFolderKeys).toContain('folder-2')
  })

  it('folder name match keeps subtree leaves', () => {
    const out = filterWorkspaceTree({ folders, leaves, query: 'ads' })
    expect(out.folders.map(f => f.id).sort()).toEqual([1, 3])
    expect(out.leaves.map(l => l.id)).toEqual([11])
  })

  it('no match returns empty sets', () => {
    const out = filterWorkspaceTree({ folders, leaves, query: 'zzz_none' })
    expect(out.folders).toEqual([])
    expect(out.leaves).toEqual([])
  })

  it('matches root leaf without folders', () => {
    const out = filterWorkspaceTree({ folders, leaves, query: 'root_script' })
    expect(out.leaves.map(l => l.id)).toEqual([12])
    expect(out.folders).toEqual([])
  })
})

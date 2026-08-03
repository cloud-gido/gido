/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  buildSortedWorkspaceTree,
  collectTreeSortViolations,
  sortByName,
} from './treeSort'

describe('treeSort', () => {
  it('按中文名称字典序，忽略 sort_order', () => {
    const sorted = sortByName([
      { id: 3, name: 'zeta', sort_order: 10 },
      { id: 1, name: 'alpha', sort_order: 30 },
      { id: 2, name: 'beta', sort_order: 20 },
    ] as { id: number; name: string; sort_order?: number }[])
    expect(sorted.map(x => x.name)).toEqual(['alpha', 'beta', 'zeta'])
  })

  it('同名时按 id 稳定排序', () => {
    expect(
      sortByName([
        { id: 20, name: 'a' },
        { id: 10, name: 'a' },
      ]).map(x => x.id),
    ).toEqual([10, 20])
  })

  it('递归：每一层目录在前、字典序（对齐截图 ads/dw 结构）', () => {
    // 故意打乱插入顺序，模拟 API 返回顺序
    const folders = [
      { id: 6, name: 'goodvideo', parent_id: 3 },
      { id: 7, name: 'gameline', parent_id: 3 },
      { id: 3, name: 'dws', parent_id: 2 },
      { id: 4, name: 'dim', parent_id: 2 },
      { id: 5, name: 'dwd', parent_id: 2 },
      { id: 2, name: 'dw', parent_id: null },
      { id: 8, name: 'goodvideo', parent_id: 1 },
      { id: 9, name: 'gameline', parent_id: 1 },
      { id: 1, name: 'ads', parent_id: null },
      { id: 10, name: 'ods', parent_id: null },
      { id: 11, name: 'Good_Videos', parent_id: null },
      { id: 12, name: '个人目录', parent_id: null },
      { id: 13, name: '演示目录', parent_id: null },
      { id: 14, name: 'goodvideo', parent_id: 5 },
      { id: 15, name: 'gameline', parent_id: 5 },
      { id: 16, name: 'goodvideo', parent_id: 4 },
      { id: 17, name: 'gameline', parent_id: 4 },
    ] as { id: number; name: string; parent_id: number | null }[]

    const leaves = [
      { id: 101, name: 'z_script', folder_id: 7 },
      { id: 100, name: 'a_script', folder_id: 7 },
      { id: 102, name: 'root_b', folder_id: null },
      { id: 103, name: 'root_a', folder_id: null },
    ] as { id: number; name: string; folder_id: number | null }[]

    const tree = buildSortedWorkspaceTree({ folders, leaves })
    expect(collectTreeSortViolations(tree)).toEqual([])

    // 根：zh-CN 字典序（中文名通常在拉丁名之前），再叶子
    expect(tree.filter(n => n.kind === 'folder').map(n => n.name)).toEqual([
      '个人目录',
      '演示目录',
      'ads',
      'dw',
      'Good_Videos',
      'ods',
    ])
    expect(tree.filter(n => n.kind === 'leaf').map(n => n.name)).toEqual(['root_a', 'root_b'])

    const ads = tree.find(n => n.name === 'ads')!
    expect(ads.children.map(n => n.name)).toEqual(['gameline', 'goodvideo'])

    const dw = tree.find(n => n.name === 'dw')!
    expect(dw.children.map(n => n.name)).toEqual(['dim', 'dwd', 'dws'])

    const dws = dw.children.find(n => n.name === 'dws')!
    expect(dws.children.map(n => n.name)).toEqual(['gameline', 'goodvideo'])

    const gamelineUnderDws = dws.children.find(n => n.name === 'gameline')!
    expect(gamelineUnderDws.children.map(n => n.name)).toEqual(['a_script', 'z_script'])
  })
})

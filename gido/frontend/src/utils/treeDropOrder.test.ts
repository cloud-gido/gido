/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  ancestorFolderKeys,
  antdGapRelative,
  folderReorderNeedsReparent,
  insertAmongPeers,
  orderLeavesAfterDrop,
  resolveFolderDropIntent,
} from './treeDropOrder'

describe('treeDropOrder (Ant Design / IDEA)', () => {
  it('antdGapRelative：官方 relative===-1 表示目标上方', () => {
    expect(antdGapRelative(0, '0-0-1')).toBe(-1)
    expect(antdGapRelative(2, '0-0-1')).toBe(1)
  })

  it('目录同级：插到 relative 之前/之后', () => {
    expect(
      insertAmongPeers({
        peerIdsExcludingDragged: [1, 3],
        draggedId: 2,
        relativeId: 3,
        position: 'before',
      }),
    ).toEqual([1, 2, 3])
    expect(
      insertAmongPeers({
        peerIdsExcludingDragged: [1, 3],
        draggedId: 2,
        relativeId: 3,
        position: 'after',
      }),
    ).toEqual([1, 3, 2])
  })

  it('无 relative 时追加到末尾', () => {
    expect(
      insertAmongPeers({
        peerIdsExcludingDragged: ['a', 'b'] as string[],
        draggedId: 'c',
        relativeId: null,
        position: 'after',
      }),
    ).toEqual(['a', 'b', 'c'])
  })

  it('拖到目录上（!dropToGap）→ 迁入（IDEA）', () => {
    expect(
      resolveFolderDropIntent({
        draggedId: 2,
        draggedParentId: null,
        dropKey: 'folder-1',
        dropToGap: false,
        dropPosition: 0,
        nodePos: '0-0-0',
        folders: [
          { id: 1, parent_id: null },
          { id: 2, parent_id: null },
        ] as { id: number; parent_id: number | null }[],
      }),
    ).toEqual({ kind: 'reparent', targetParentId: 1 })
  })

  it('缝隙 relative===-1 → 目标前；否则目标后（Ant 官方）', () => {
    const folders = [
      { id: 1, parent_id: null },
      { id: 2, parent_id: null },
    ] as { id: number; parent_id: number | null }[]
    const before = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: null,
      dropKey: 'folder-1',
      dropToGap: true,
      dropPosition: 0,
      nodePos: '0-0-1',
      folders,
    })
    expect(before).toEqual({
      kind: 'reorder',
      parentId: null,
      relativeId: 1,
      position: 'before',
    })
    const after = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: null,
      dropKey: 'folder-1',
      dropToGap: true,
      dropPosition: 2,
      nodePos: '0-0-1',
      folders,
    })
    expect(after).toEqual({
      kind: 'reorder',
      parentId: null,
      relativeId: 1,
      position: 'after',
    })
  })

  it('子目录拖到根 → 需先 reparent', () => {
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: 1,
      dropKey: 'root',
      dropToGap: true,
      dropPosition: 0,
      nodePos: '0-0',
      folders: [
        { id: 1, parent_id: null },
        { id: 2, parent_id: 1 },
      ] as { id: number; parent_id: number | null }[],
    })
    expect(intent).toMatchObject({ kind: 'reorder', parentId: null })
    if (intent?.kind === 'reorder') {
      expect(folderReorderNeedsReparent(1, intent)).toBe(true)
    }
  })

  it('子目录拖到父目录上方缝 → 提到与父同级且在前', () => {
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: 1,
      dropKey: 'folder-1',
      dropToGap: true,
      dropPosition: 0,
      nodePos: '0-0-1',
      folders: [
        { id: 1, parent_id: null },
        { id: 2, parent_id: 1 },
        { id: 3, parent_id: null },
      ] as { id: number; parent_id: number | null }[],
    })
    expect(intent).toEqual({
      kind: 'reorder',
      parentId: null,
      relativeId: 1,
      position: 'before',
    })
    if (intent?.kind === 'reorder') {
      expect(folderReorderNeedsReparent(1, intent)).toBe(true)
    }
  })

  it('叶子落到目录内：非 gap 置顶，gap 追加', () => {
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [10, 20] as number[],
        draggedId: 30,
        dropRelativeLeafId: null,
        dropToGap: false,
      }),
    ).toEqual([30, 10, 20])
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [10, 20] as number[],
        draggedId: 30,
        dropRelativeLeafId: null,
        dropToGap: true,
      }),
    ).toEqual([10, 20, 30])
  })

  it('定位：展开祖先目录 key', () => {
    expect(
      ancestorFolderKeys({
        leafFolderId: 3,
        folders: [
          { id: 1, parent_id: null },
          { id: 2, parent_id: 1 },
          { id: 3, parent_id: 2 },
        ] as { id: number; parent_id: number | null }[],
      }),
    ).toEqual(['root', 'folder-3', 'folder-2', 'folder-1'])
  })
})

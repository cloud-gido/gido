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
  pickVisualDropKey,
  positionByPointerHalf,
  resolveFolderDropIntent,
} from './treeDropOrder'

describe('treeDropOrder (Explorer / IDEA)', () => {
  it('指针上半 before、下半 after', () => {
    const rect = { top: 100, height: 40 }
    expect(positionByPointerHalf(110, rect)).toBe('before')
    expect(positionByPointerHalf(130, rect)).toBe('after')
  })

  it('pickVisualDropKey 优先悬停行而非 antd 改写后的 dropKey', () => {
    expect(
      pickVisualDropKey({
        hoverKey: 'folder-1',
        pointKey: 'folder-1',
        antdDropKey: 'folder-10',
        dragKey: 'folder-2',
      }),
    ).toBe('folder-1')
  })

  it('antdGapRelative 回退', () => {
    expect(antdGapRelative(0, '0-0-1')).toBe(-1)
    expect(antdGapRelative(2, '0-0-1')).toBe(1)
  })

  it('同级拖到目标上半 → 提到前面（含首个子目录）', () => {
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: 10,
      dropKey: 'folder-1',
      dropToGap: false,
      dropPosition: 0,
      nodePos: '0-0-0-0',
      folders: [
        { id: 10, parent_id: null },
        { id: 1, parent_id: 10 },
        { id: 2, parent_id: 10 },
      ] as { id: number; parent_id: number | null }[],
      clientY: 110,
      dropNodeRect: { top: 100, height: 40 },
    })
    expect(intent).toEqual({
      kind: 'reorder',
      parentId: 10,
      relativeId: 1,
      position: 'before',
    })
    expect(
      insertAmongPeers({
        peerIdsExcludingDragged: [1],
        draggedId: 2,
        relativeId: 1,
        position: 'before',
      }),
    ).toEqual([2, 1])
  })

  it('同级拖到目标下半 → 放到后面', () => {
    const intent = resolveFolderDropIntent({
      draggedId: 1,
      draggedParentId: 10,
      dropKey: 'folder-2',
      dropToGap: true,
      dropPosition: 1,
      nodePos: '0-0-0-1',
      folders: [
        { id: 10, parent_id: null },
        { id: 1, parent_id: 10 },
        { id: 2, parent_id: 10 },
      ] as { id: number; parent_id: number | null }[],
      clientY: 130,
      dropNodeRect: { top: 100, height: 40 },
    })
    expect(intent).toEqual({
      kind: 'reorder',
      parentId: 10,
      relativeId: 2,
      position: 'after',
    })
  })

  it('同级按住 Alt → 迁入目标', () => {
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
        nestModifier: true,
      }),
    ).toEqual({ kind: 'reparent', targetParentId: 1 })
  })

  it('非同级拖到目录上 → 迁入', () => {
    expect(
      resolveFolderDropIntent({
        draggedId: 2,
        draggedParentId: 9,
        dropKey: 'folder-1',
        dropToGap: false,
        dropPosition: 0,
        nodePos: '0-0-0',
        folders: [
          { id: 1, parent_id: null },
          { id: 9, parent_id: null },
          { id: 2, parent_id: 9 },
        ] as { id: number; parent_id: number | null }[],
      }),
    ).toEqual({ kind: 'reparent', targetParentId: 1 })
  })

  it('子目录拖到根 → 需 reparent', () => {
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
    expect(intent?.kind).toBe('reorder')
    if (intent?.kind === 'reorder') {
      expect(folderReorderNeedsReparent(1, intent)).toBe(true)
    }
  })

  it('insertAmongPeers / leaves / ancestors', () => {
    expect(
      insertAmongPeers({
        peerIdsExcludingDragged: [1, 3],
        draggedId: 2,
        relativeId: 3,
        position: 'before',
      }),
    ).toEqual([1, 2, 3])
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [10, 20] as number[],
        draggedId: 30,
        dropRelativeLeafId: null,
        dropToGap: false,
      }),
    ).toEqual([30, 10, 20])
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

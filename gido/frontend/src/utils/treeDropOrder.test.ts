/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { ancestorFolderKeys, folderReorderNeedsReparent, insertAmongPeers, orderLeavesAfterDrop, reorderPeerIdsByDrop, resolveFolderDropIntent } from './treeDropOrder'

describe('treeDropOrder', () => {
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

  it('把 b 拖到未展开的同级 a 上（dropToGap=false）→ 排到 a 前', () => {
    const folders = [
      { id: 1 as number, parent_id: null as number | null },
      { id: 2 as number, parent_id: null as number | null },
    ]
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: null,
      dropKey: 'folder-1',
      dropToGap: false,
      dropPosition: 0,
      nodePos: '0-0-0',
      folders,
      dropFolderExpanded: false,
    })
    expect(intent).toEqual({
      kind: 'reorder',
      parentId: null,
      relativeId: 1,
      position: 'before',
    })
    expect(
      insertAmongPeers({
        peerIdsExcludingDragged: [1] as number[],
        draggedId: 2,
        relativeId: 1,
        position: 'before',
      }),
    ).toEqual([2, 1])
  })

  it('缝隙拖到 a 后 → after', () => {
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: null,
      dropKey: 'folder-1',
      dropToGap: true,
      dropPosition: 2,
      nodePos: '0-0-1',
      folders: [
        { id: 1, parent_id: null },
        { id: 2, parent_id: null },
      ] as { id: number; parent_id: number | null }[],
    })
    expect(intent?.kind).toBe('reorder')
    if (intent?.kind === 'reorder') {
      expect(intent.position).toBe('after')
      expect(intent.relativeId).toBe(1)
    }
  })

  it('拖到已展开的同级目录内容上 → 嵌套为子目录', () => {
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
        dropFolderExpanded: true,
      }),
    ).toEqual({ kind: 'reparent', targetParentId: 1 })
  })

  it('子目录拖到根 → 目标父级为 null，且需要先 reparent', () => {
    const folders = [
      { id: 1, parent_id: null },
      { id: 2, parent_id: 1 },
    ] as { id: number; parent_id: number | null }[]
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: 1,
      dropKey: 'root',
      dropToGap: true,
      dropPosition: 0,
      nodePos: '0-0',
      folders,
    })
    expect(intent).toMatchObject({ kind: 'reorder', parentId: null })
    if (intent?.kind === 'reorder') {
      expect(folderReorderNeedsReparent(1, intent)).toBe(true)
    }
  })

  it('子目录拖到父目录旁缝隙 → 提到与父同级', () => {
    const folders = [
      { id: 1, parent_id: null },
      { id: 2, parent_id: 1 },
      { id: 3, parent_id: null },
    ] as { id: number; parent_id: number | null }[]
    // 拖 folder-2 到 folder-1 上方缝隙
    const intent = resolveFolderDropIntent({
      draggedId: 2,
      draggedParentId: 1,
      dropKey: 'folder-1',
      dropToGap: true,
      dropPosition: 0,
      nodePos: '0-0-0',
      folders,
    })
    expect(intent).toEqual({
      kind: 'reorder',
      parentId: null,
      relativeId: 1,
      position: 'before',
    })
    if (intent?.kind === 'reorder') {
      expect(folderReorderNeedsReparent(1, intent)).toBe(true)
      expect(
        insertAmongPeers({
          peerIdsExcludingDragged: [1, 3],
          draggedId: 2,
          relativeId: 1,
          position: 'before',
        }),
      ).toEqual([2, 1, 3])
    }
  })

  it('把 b 拖到 a|b 中间缝（antd 标成 a 后）→ 仍得到 b|a', () => {
    expect(
      reorderPeerIdsByDrop({
        peerIdsInDisplayOrder: [1, 2] as number[],
        draggedId: 2,
        dropId: 1,
        relativeDrop: 1,
        dropToGap: true,
      }),
    ).toEqual([2, 1])
  })

  it('把 b 拖到 a 上（非缝）→ b|a', () => {
    expect(
      reorderPeerIdsByDrop({
        peerIdsInDisplayOrder: [1, 2] as number[],
        draggedId: 2,
        dropId: 1,
        relativeDrop: 0,
        dropToGap: false,
      }),
    ).toEqual([2, 1])
  })

  it('三层上拖到更上方缝：a 与 b 之间放 c → a|c|b', () => {
    expect(
      reorderPeerIdsByDrop({
        peerIdsInDisplayOrder: [1, 2, 3] as number[],
        draggedId: 3,
        dropId: 1,
        relativeDrop: 1,
        dropToGap: true,
      }),
    ).toEqual([1, 3, 2])
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

  it('叶子相对另一叶子：dropPositionHint 决定前后', () => {
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [1, 3],
        draggedId: 2,
        dropRelativeLeafId: 1,
        dropToGap: true,
        dropPositionHint: 0,
      }),
    ).toEqual([2, 1, 3])
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [1, 3],
        draggedId: 2,
        dropRelativeLeafId: 1,
        dropToGap: true,
        dropPositionHint: 2,
      }),
    ).toEqual([1, 2, 3])
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

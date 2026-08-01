/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { ancestorFolderKeys, insertAmongPeers, orderLeavesAfterDrop } from './treeDropOrder'

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
        peerIdsExcludingDragged: ['a', 'b'],
        draggedId: 'c',
        relativeId: null,
        position: 'after',
      }),
    ).toEqual(['a', 'b', 'c'])
  })

  it('叶子落到目录内：非 gap 置顶，gap 追加', () => {
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [10, 20],
        draggedId: 30,
        dropRelativeLeafId: null,
        dropToGap: false,
      }),
    ).toEqual([30, 10, 20])
    expect(
      orderLeavesAfterDrop({
        peerIdsExcludingDragged: [10, 20],
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
        ],
      }),
    ).toEqual(['root', 'folder-3', 'folder-2', 'folder-1'])
  })
})

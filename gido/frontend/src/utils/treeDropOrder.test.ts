/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  ancestorFolderKeys,
  pickVisualDropKey,
  resolveFolderMoveIntent,
  resolveLeafMoveTarget,
} from './treeDropOrder'

describe('treeDropOrder (OS move-only)', () => {
  const folders = [
    { id: 10, parent_id: null },
    { id: 1, parent_id: 10 },
    { id: 2, parent_id: 10 },
  ] as { id: number; parent_id: number | null }[]

  it('pickVisualDropKey 优先悬停行', () => {
    expect(
      pickVisualDropKey({
        hoverKey: 'folder-1',
        pointKey: 'folder-1',
        antdDropKey: 'folder-10',
        dragKey: 'folder-2',
      }),
    ).toBe('folder-1')
  })

  it('拖到目录上 → 迁入', () => {
    expect(
      resolveFolderMoveIntent({
        draggedId: 2,
        draggedParentId: 10,
        dropKey: 'folder-1',
        dropToGap: false,
        folders,
      }),
    ).toEqual({ targetParentId: 1 })
  })

  it('同级缝隙 → 不排序（noop）', () => {
    expect(
      resolveFolderMoveIntent({
        draggedId: 2,
        draggedParentId: 10,
        dropKey: 'folder-1',
        dropToGap: true,
        folders,
      }),
    ).toBeNull()
  })

  it('拖到根 → 提到根', () => {
    expect(
      resolveFolderMoveIntent({
        draggedId: 2,
        draggedParentId: 10,
        dropKey: 'root',
        dropToGap: true,
        folders,
      }),
    ).toEqual({ targetParentId: null })
  })

  it('已在根再拖到根 → noop', () => {
    expect(
      resolveFolderMoveIntent({
        draggedId: 10,
        draggedParentId: null,
        dropKey: 'root',
        dropToGap: true,
        folders,
      }),
    ).toBeNull()
  })

  it('叶子拖到目录上 → 迁入；同级 → noop', () => {
    expect(
      resolveLeafMoveTarget({
        draggedFolderId: null,
        dropKey: 'folder-1',
        dropToGap: false,
        folders,
      }),
    ).toBe(1)
    expect(
      resolveLeafMoveTarget({
        draggedFolderId: 10,
        dropKey: 'folder-1',
        dropToGap: true,
        folders,
      }),
    ).toBeUndefined()
  })

  it('ancestorFolderKeys', () => {
    expect(
      ancestorFolderKeys({
        leafFolderId: 2,
        folders: [
          { id: 1, parent_id: null },
          { id: 2, parent_id: 1 },
        ] as { id: number; parent_id: number | null }[],
      }),
    ).toEqual(['root', 'folder-2', 'folder-1'])
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树拖拽落点（对齐 Ant Design Tree 官方示例 + IDEA Project 视图，无额外启发式）。
 *
 * - 缝隙 dropToGap：与目标同级；relative===-1 插到目标前，否则目标后（可顺带换父级=提出）
 * - 落到目录上 !dropToGap：迁入该目录（嵌套）
 * - 落到根：提到根层后再按位置排序
 */

export type DropPosition = 'before' | 'after'

export type FolderDropIntent<T extends string | number> =
  | { kind: 'reorder'; parentId: T | null; relativeId: T | null; position: DropPosition; insertIndex?: number }
  | { kind: 'reparent'; targetParentId: T | null }

function sameId(a: string | number | null | undefined, b: string | number | null | undefined): boolean {
  if (a == null && b == null) return true
  if (a == null || b == null) return false
  return String(a) === String(b)
}

/** Ant Design Tree：dropPosition - node.pos 末段；官方示例用 === -1 表示落在目标上方 */
export function antdGapRelative(dropPosition: number, nodePos?: string): number {
  const dropPosParts = String(nodePos || '').split('-')
  const nodeIndex = Number(dropPosParts[dropPosParts.length - 1] || 0)
  return dropPosition - nodeIndex
}

export function resolveFolderDropIntent<T extends string | number>(opts: {
  draggedId: T
  draggedParentId: T | null
  dropKey: string
  dropToGap: boolean
  dropPosition: number
  nodePos?: string
  folders: { id: T; parent_id: T | null }[]
  /** 落到叶子上时，该叶子所属目录 */
  dropLeafFolderId?: T | null
}): FolderDropIntent<T> | null {
  const {
    draggedId,
    draggedParentId,
    dropKey,
    dropToGap,
    dropPosition,
    nodePos,
    folders,
    dropLeafFolderId,
  } = opts

  const relative = antdGapRelative(dropPosition, nodePos)
  const gapPosition: DropPosition = relative === -1 ? 'before' : 'after'

  if (dropKey === 'root') {
    return {
      kind: 'reorder',
      parentId: null,
      relativeId: null,
      position: 'after',
      insertIndex: Math.max(0, dropPosition),
    }
  }

  if (!dropKey.startsWith('folder-')) {
    const parentId = (dropLeafFolderId ?? null) as T | null
    if (!sameId(draggedParentId, parentId)) {
      return { kind: 'reparent', targetParentId: parentId }
    }
    if (dropToGap) {
      return {
        kind: 'reorder',
        parentId,
        relativeId: null,
        position: gapPosition,
        insertIndex: Math.max(0, dropPosition),
      }
    }
    return { kind: 'reparent', targetParentId: parentId }
  }

  const raw = dropKey.slice('folder-'.length)
  const dropFolder = folders.find(f => String(f.id) === raw)
  if (!dropFolder) return null
  const dropFolderId = dropFolder.id
  if (sameId(dropFolderId, draggedId)) return null

  const dropParentId = (dropFolder.parent_id ?? null) as T | null

  if (dropToGap) {
    // 官方 / IDEA：缝隙 = 与该节点同级前后（子目录拖到父旁缝即可提出）
    return {
      kind: 'reorder',
      parentId: dropParentId,
      relativeId: dropFolderId,
      position: gapPosition,
    }
  }

  // IDEA：拖到目录上 = 迁入该目录
  return { kind: 'reparent', targetParentId: dropFolderId }
}

/** 若「排序」目标父级与当前父级不同，须先 reparent（提出/迁入）再写同级顺序 */
export function folderReorderNeedsReparent<T extends string | number>(
  draggedParentId: T | null,
  intent: Extract<FolderDropIntent<T>, { kind: 'reorder' }>,
): boolean {
  return !sameId(draggedParentId, intent.parentId)
}

/** 同级插入：把 draggedId 插到 relativeId 前/后；无 relative 则按 insertIndex 或追加 */
export function insertAmongPeers<T extends string | number>(opts: {
  peerIdsExcludingDragged: T[]
  draggedId: T
  relativeId: T | null
  position: DropPosition
  insertIndex?: number
}): T[] {
  const ordered = [...opts.peerIdsExcludingDragged]
  if (opts.relativeId != null) {
    const idx = ordered.findIndex(id => String(id) === String(opts.relativeId))
    const insertAt = idx < 0 ? ordered.length : (opts.position === 'before' ? idx : idx + 1)
    ordered.splice(insertAt, 0, opts.draggedId)
  } else if (opts.insertIndex != null && Number.isFinite(opts.insertIndex)) {
    const at = Math.max(0, Math.min(Math.floor(opts.insertIndex), ordered.length))
    ordered.splice(at, 0, opts.draggedId)
  } else {
    ordered.push(opts.draggedId)
  }
  return ordered
}

/**
 * 叶子拖到目录/根/另一叶子后的有序 ID。
 * dropToGap=false 且落到目录/根：插到目标目录内首位；否则追加。
 */
export function orderLeavesAfterDrop<T extends string | number>(opts: {
  peerIdsExcludingDragged: T[]
  draggedId: T
  dropRelativeLeafId: T | null
  dropToGap: boolean
  dropPositionHint?: number
  relativeIndexInPeers?: number
}): T[] {
  const { peerIdsExcludingDragged, draggedId, dropRelativeLeafId, dropToGap } = opts
  if (dropRelativeLeafId != null) {
    const idx = peerIdsExcludingDragged.findIndex(id => String(id) === String(dropRelativeLeafId))
    const ordered = [...peerIdsExcludingDragged]
    if (idx >= 0) {
      const hint = opts.dropPositionHint
      const insertAt =
        hint != null && hint > idx ? idx + 1 : idx
      ordered.splice(insertAt, 0, draggedId)
    } else {
      ordered.push(draggedId)
    }
    return ordered
  }
  return dropToGap ? [...peerIdsExcludingDragged, draggedId] : [draggedId, ...peerIdsExcludingDragged]
}

/** 展开叶节点祖先目录 key（含 root） */
export function ancestorFolderKeys<T extends string | number>(opts: {
  leafFolderId: T | null | undefined
  folders: { id: T; parent_id: T | null }[]
}): string[] {
  const keys: string[] = ['root']
  let fid: T | null | undefined = opts.leafFolderId ?? null
  const byId = new Map(opts.folders.map(f => [String(f.id), f]))
  while (fid != null) {
    keys.push(`folder-${fid}`)
    const f = byId.get(String(fid))
    fid = f?.parent_id ?? null
  }
  return keys
}

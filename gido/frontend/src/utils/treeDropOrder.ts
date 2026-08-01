/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树拖拽落点 → 同级有序 ID 列表（纯函数，供 WorkspaceFolderTree 与单测共用）。
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

/**
 * 解析目录拖拽意图（对齐 IDEA Project 视图：可拖出到父级/根，也可嵌套进目录）。
 *
 * - 拖到根 / 拖到某目录的同级缝隙 → 目标 parent 为该层；若当前不在该层，调用方须先 reparent 再排序
 * - 拖到未展开的同级目录上 → 同级排序
 * - 拖到已展开目录内容 / 非同级目录上 → 嵌套为子目录
 */
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
  /** 目标目录当前是否展开；未传则视为未展开 */
  dropFolderExpanded?: boolean
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
    dropFolderExpanded = false,
  } = opts

  const dropPosParts = String(nodePos || '').split('-')
  const nodeIndex = Number(dropPosParts[dropPosParts.length - 1] || 0)
  const relative = dropPosition - nodeIndex
  const gapPosition: DropPosition = relative <= 0 ? 'before' : 'after'

  if (dropKey === 'root') {
    // 拖到根：提到最外层（若本就在根则仅排序）
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
    return {
      kind: 'reorder',
      parentId,
      relativeId: null,
      position: 'after',
      insertIndex: Math.max(0, dropPosition),
    }
  }

  const raw = dropKey.slice('folder-'.length)
  const dropFolder = folders.find(f => String(f.id) === raw)
  if (!dropFolder) return null
  const dropFolderId = dropFolder.id
  if (sameId(dropFolderId, draggedId)) return null

  const dropParentId = (dropFolder.parent_id ?? null) as T | null
  const sameLevel = sameId(draggedParentId, dropParentId)

  if (dropToGap) {
    // 缝隙 = 与 drop 目录同级；子目录拖到父目录旁即可「提出来」（IDEA 同款）
    return {
      kind: 'reorder',
      parentId: dropParentId,
      relativeId: dropFolderId,
      position: gapPosition,
    }
  }

  // 落到目录内容上：同级且未展开 → 排序；否则嵌套为子目录
  if (sameLevel && !dropFolderExpanded) {
    return {
      kind: 'reorder',
      parentId: dropParentId,
      relativeId: dropFolderId,
      position: relative > 0 ? 'after' : 'before',
    }
  }

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
  /** Ant Tree dropPosition 与节点 index 差值语义：用于相对叶子插入时的前后 */
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

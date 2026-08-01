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
 * 解析目录拖拽意图。
 * Ant Tree 把「拖到同级目录节点上」常标成 dropToGap=false（嵌套）；
 * 对同级且目标未展开，按「排序到目标前/后」处理，避免只能嵌套、无法 a|b 换序。
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
    return {
      kind: 'reorder',
      parentId: null,
      relativeId: null,
      position: 'after',
      // root 下 children 含目录+叶子，dropPosition 近似插入下标；目录排序时按目录同级列表裁剪
      insertIndex: Math.max(0, dropPosition),
    }
  }

  if (!dropKey.startsWith('folder-')) {
    // 落到脚本/作业叶子：在叶子所在目录的同级目录末尾排序（或已在该父级则按 insertIndex）
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
    return {
      kind: 'reorder',
      parentId: dropParentId,
      relativeId: dropFolderId,
      position: gapPosition,
    }
  }

  // 落到目录内容上：同级且未展开 → 排序（常见「把 b 拖到 a 上/前」）；否则嵌套为子目录
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

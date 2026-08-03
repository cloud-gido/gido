/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树拖拽（对齐操作系统文件管理器 / IDEA）：
 *
 * - 同级不排序：展示固定为目录在前、脚本在后，组内字典序
 * - 拖到目录上 → 迁入该目录
 * - 拖到根 / 拖到某层缝隙 → 移到该层（换父级）
 * - 同级缝隙拖放 → 无操作（不支持手工排序）
 *
 * 注意：rc-tree 会改写 dropTarget；须用真实悬停行（onDragOver / elementFromPoint）。
 */

function sameId(a: string | number | null | undefined, b: string | number | null | undefined): boolean {
  if (a == null && b == null) return true
  if (a == null || b == null) return false
  return String(a) === String(b)
}

/** 优先真实悬停行，其次落点元素，最后才用 antd 可能改写过的 dropKey */
export function pickVisualDropKey(opts: {
  hoverKey?: string | null
  pointKey?: string | null
  antdDropKey: string
  dragKey?: string | null
}): string {
  for (const k of [opts.hoverKey, opts.pointKey, opts.antdDropKey]) {
    if (!k || k === opts.dragKey) continue
    return k
  }
  return opts.antdDropKey
}

/**
 * 目录拖放：只产生「换父级 / 迁入」，不产生同级排序。
 * 返回 null 表示同级无效拖放（应提示按名称自动排序）。
 */
export function resolveFolderMoveIntent<T extends string | number>(opts: {
  draggedId: T
  draggedParentId: T | null
  dropKey: string
  /** true = 缝隙；false = 落在节点上 */
  dropToGap: boolean
  folders: { id: T; parent_id: T | null }[]
  dropLeafFolderId?: T | null
}): { targetParentId: T | null } | null {
  const { draggedId, draggedParentId, dropKey, dropToGap, folders, dropLeafFolderId } = opts

  if (dropKey === 'root') {
    if (draggedParentId == null) return null
    return { targetParentId: null }
  }

  if (!dropKey.startsWith('folder-')) {
    const parentId = (dropLeafFolderId ?? null) as T | null
    if (sameId(draggedParentId, parentId)) return null
    return { targetParentId: parentId }
  }

  const raw = dropKey.slice('folder-'.length)
  const dropFolder = folders.find(f => String(f.id) === raw)
  if (!dropFolder) return null
  if (sameId(dropFolder.id, draggedId)) return null

  if (!dropToGap) {
    // 拖到目录上 → 迁入
    if (sameId(draggedParentId, dropFolder.id)) return null
    return { targetParentId: dropFolder.id }
  }

  // 缝隙：移到与目标目录同级（换父级）；已同级则不排序
  const newParent = (dropFolder.parent_id ?? null) as T | null
  if (sameId(draggedParentId, newParent)) return null
  return { targetParentId: newParent }
}

/**
 * 叶子拖放目标父目录；同级无效时返回 null。
 */
export function resolveLeafMoveTarget<T extends string | number>(opts: {
  draggedFolderId: T | null
  dropKey: string
  dropToGap: boolean
  folders: { id: T; parent_id: T | null }[]
  dropLeafFolderId?: T | null
}): T | null | undefined {
  // undefined = 无效同级；null = 根；T = 某目录
  const { draggedFolderId, dropKey, dropToGap, folders, dropLeafFolderId } = opts

  if (dropKey === 'root') {
    if (draggedFolderId == null) return undefined
    return null
  }

  if (dropKey.startsWith('folder-')) {
    const raw = dropKey.slice('folder-'.length)
    const dropFolder = folders.find(f => String(f.id) === raw)
    if (!dropFolder) return undefined
    if (!dropToGap) {
      if (sameId(draggedFolderId, dropFolder.id)) return undefined
      return dropFolder.id
    }
    const newParent = (dropFolder.parent_id ?? null) as T | null
    if (sameId(draggedFolderId, newParent)) return undefined
    return newParent
  }

  const parentId = (dropLeafFolderId ?? null) as T | null
  if (sameId(draggedFolderId, parentId)) return undefined
  return parentId
}

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

// —— 以下保留轻量兼容导出，避免外部旧引用瞬间炸掉 ——

/** @deprecated 同级不再排序 */
export type DropPosition = 'before' | 'after'

/** @deprecated 使用 resolveFolderMoveIntent */
export type FolderDropIntent<T extends string | number> =
  | { kind: 'reorder'; parentId: T | null; relativeId: T | null; position: DropPosition; insertIndex?: number }
  | { kind: 'reparent'; targetParentId: T | null }

/** @deprecated */
export function antdGapRelative(dropPosition: number, nodePos?: string): number {
  const dropPosParts = String(nodePos || '').split('-')
  const nodeIndex = Number(dropPosParts[dropPosParts.length - 1] || 0)
  return dropPosition - nodeIndex
}

/** @deprecated */
export function positionByPointerHalf(
  clientY: number | null | undefined,
  dropNodeRect: { top: number; height: number } | null | undefined,
): DropPosition | null {
  if (clientY == null || !dropNodeRect) return null
  if (!(dropNodeRect.height > 0)) return null
  return clientY < dropNodeRect.top + dropNodeRect.height / 2 ? 'before' : 'after'
}

/** @deprecated 使用 resolveFolderMoveIntent */
export function resolveFolderDropIntent<T extends string | number>(opts: {
  draggedId: T
  draggedParentId: T | null
  dropKey: string
  dropToGap: boolean
  dropPosition: number
  nodePos?: string
  folders: { id: T; parent_id: T | null }[]
  dropLeafFolderId?: T | null
  nestModifier?: boolean
  clientY?: number | null
  dropNodeRect?: { top: number; height: number } | null
}): FolderDropIntent<T> | null {
  const moved = resolveFolderMoveIntent({
    draggedId: opts.draggedId,
    draggedParentId: opts.draggedParentId,
    dropKey: opts.dropKey,
    dropToGap: opts.nestModifier ? false : opts.dropToGap,
    folders: opts.folders,
    dropLeafFolderId: opts.dropLeafFolderId,
  })
  if (!moved) return null
  return { kind: 'reparent', targetParentId: moved.targetParentId }
}

/** @deprecated */
export function folderReorderNeedsReparent<T extends string | number>(
  draggedParentId: T | null,
  intent: Extract<FolderDropIntent<T>, { kind: 'reorder' }>,
): boolean {
  return !sameId(draggedParentId, intent.parentId)
}

/** @deprecated */
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

/** @deprecated */
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
      const insertAt = hint != null && hint > idx ? idx + 1 : idx
      ordered.splice(insertAt, 0, draggedId)
    } else {
      ordered.push(draggedId)
    }
    return ordered
  }
  return dropToGap ? [...peerIdsExcludingDragged, draggedId] : [draggedId, ...peerIdsExcludingDragged]
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树拖拽（对齐常见桌面文件管理器，而非自创启发式）：
 *
 * - 同级目录之间：按指针落在目标行上半/下半 → 插到前/后（Windows 资源管理器「未排序」列表同款）
 * - 拖到非同级目录上，或按住 Alt/Option 拖到目录上 → 迁入该目录（IDEA Move into）
 * - 拖到根 / 父级旁 → 提到该层
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

/** Ant Design：dropPosition - node.pos 末段（仅作指针不可用时的回退） */
export function antdGapRelative(dropPosition: number, nodePos?: string): number {
  const dropPosParts = String(nodePos || '').split('-')
  const nodeIndex = Number(dropPosParts[dropPosParts.length - 1] || 0)
  return dropPosition - nodeIndex
}

/**
 * 指针在目标行上半 → before，下半 → after（资源管理器列表重排标准做法）。
 * 无法取几何信息时返回 null，由调用方回退 antd relative。
 */
export function positionByPointerHalf(
  clientY: number | null | undefined,
  dropNodeRect: { top: number; height: number } | null | undefined,
): DropPosition | null {
  if (clientY == null || !dropNodeRect) return null
  if (!(dropNodeRect.height > 0)) return null
  return clientY < dropNodeRect.top + dropNodeRect.height / 2 ? 'before' : 'after'
}

export function resolveFolderDropIntent<T extends string | number>(opts: {
  draggedId: T
  draggedParentId: T | null
  dropKey: string
  dropToGap: boolean
  dropPosition: number
  nodePos?: string
  folders: { id: T; parent_id: T | null }[]
  dropLeafFolderId?: T | null
  /** 按住 Alt/Option：同级也迁入目标目录 */
  nestModifier?: boolean
  /** 指针 Y；与 dropNodeRect 一起用于同级前后 */
  clientY?: number | null
  dropNodeRect?: { top: number; height: number } | null
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
    nestModifier = false,
    clientY,
    dropNodeRect,
  } = opts

  const relative = antdGapRelative(dropPosition, nodePos)
  const fromPointer = positionByPointerHalf(clientY, dropNodeRect)
  const fallbackGap: DropPosition = relative === -1 ? 'before' : 'after'
  const siblingPos: DropPosition = fromPointer ?? fallbackGap

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
    return {
      kind: 'reorder',
      parentId,
      relativeId: null,
      position: siblingPos,
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

  // 同级：默认重排（上半前 / 下半后）；Alt = 迁入
  if (sameLevel && !nestModifier) {
    return {
      kind: 'reorder',
      parentId: dropParentId,
      relativeId: dropFolderId,
      position: siblingPos,
    }
  }

  if (dropToGap) {
    return {
      kind: 'reorder',
      parentId: dropParentId,
      relativeId: dropFolderId,
      position: siblingPos,
    }
  }

  // 非同级，或 Alt+同级 → 迁入
  return { kind: 'reparent', targetParentId: dropFolderId }
}

export function folderReorderNeedsReparent<T extends string | number>(
  draggedParentId: T | null,
  intent: Extract<FolderDropIntent<T>, { kind: 'reorder' }>,
): boolean {
  return !sameId(draggedParentId, intent.parentId)
}

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

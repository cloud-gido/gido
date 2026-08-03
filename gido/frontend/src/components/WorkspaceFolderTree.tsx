/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useMemo, useRef, useState } from 'react'
import { Button, Dropdown, Input, Tree, message } from 'antd'
import type { DataNode, TreeProps } from 'antd/es/tree'
import { FileOutlined, FolderOutlined, MoreOutlined } from '@ant-design/icons'
import { sortLeavesByOrderThenName, sortFoldersByOrderThenName } from '../utils/treeSort'
import {
  ancestorFolderKeys,
  folderReorderNeedsReparent,
  insertAmongPeers,
  pickVisualDropKey,
  positionByPointerHalf,
  resolveFolderDropIntent,
} from '../utils/treeDropOrder'

/** Studio / Stream 用 number；Probe 本地目录用 string */
export type TreeId = string | number

export type FolderRow<T extends TreeId = TreeId> = {
  id: T
  name: string
  parent_id: T | null
  sort_order?: number | null
}

export type LeafRow<T extends TreeId = TreeId> = {
  id: T
  name: string
  folder_id?: T | null
  job_type?: string | null
  sort_order?: number | null
}

function sameId(a: TreeId | null | undefined, b: TreeId | null | undefined): boolean {
  if (a == null && b == null) return true
  if (a == null || b == null) return false
  return String(a) === String(b)
}

function parseFolderKey(key: string): string | null {
  if (!key.startsWith('folder-')) return null
  return key.slice('folder-'.length)
}

function folderKeyToId<T extends TreeId>(raw: string, folders: FolderRow<T>[]): T | null {
  const hit = folders.find(f => String(f.id) === raw)
  return hit ? hit.id : null
}

function leafKeyToId<T extends TreeId>(key: string, leaves: LeafRow<T>[]): T | null {
  const hit = leaves.find(l => String(l.id) === key)
  return hit ? hit.id : null
}

function compareIdTie(a: TreeId, b: TreeId): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb
  return String(a).localeCompare(String(b), 'zh-CN', { numeric: true, sensitivity: 'base' })
}

function treeKeyFromPoint(clientX: number, clientY: number, excludeKey?: string | null): string | null {
  if (typeof document === 'undefined') return null
  const els = document.elementsFromPoint(clientX, clientY)
  for (const el of els) {
    const keyed = (el as Element).closest?.('[data-tree-key]') as HTMLElement | null
    const key = keyed?.getAttribute?.('data-tree-key')
    if (!key || key === excludeKey) continue
    return key
  }
  return null
}

function rectForTreeKey(key: string): { top: number; height: number } | null {
  if (typeof document === 'undefined') return null
  const keyed = document.querySelector(`[data-tree-key="${CSS.escape(key)}"]`) as HTMLElement | null
  const nodeEl = (keyed?.closest?.('.ant-tree-treenode') as HTMLElement | null) || keyed
  if (!nodeEl) return null
  const r = nodeEl.getBoundingClientRect()
  return { top: r.top, height: r.height }
}

type DragPointer = {
  hoverKey: string
  clientX: number
  clientY: number
  altKey: boolean
}

type Props<T extends TreeId = TreeId> = {
  rootTitle: string
  folders: FolderRow<T>[]
  leaves: LeafRow<T>[]
  expandedKeys: React.Key[]
  onExpandedKeysChange: (keys: React.Key[]) => void
  selectedLeafId: T | null
  onSelectLeaf: (leaf: LeafRow<T>) => void
  onCreateFolder: (parentId: T | null) => void
  onRenameFolder: (folderId: T, name: string) => Promise<void>
  onDeleteFolder: (folderId: T) => Promise<void>
  onRenameLeaf: (leafId: T, name: string) => Promise<void>
  onDeleteLeaf: (leaf: LeafRow<T>) => void
  onCopyLeaf?: (leaf: LeafRow<T>) => void
  onMoveAndReorder: (opts: {
    leafId: T
    targetFolderId: T | null
    orderedLeafIds: T[]
    folderChanged: boolean
  }) => Promise<void>
  /** 目录拖到其他父级 */
  onMoveFolder?: (opts: { folderId: T; targetParentId: T | null }) => Promise<void>
  /** 同级目录拖拽排序 */
  onReorderFolders?: (opts: { parentId: T | null; orderedFolderIds: T[] }) => Promise<void>
  folderMenuExtra?: (folder: FolderRow<T>) => { key: string; label: React.ReactNode; onClick?: () => void }[]
  readOnly?: boolean
  /** 根节点是否展示「新建目录」（侧栏已有按钮时可关） */
  showRootCreateButton?: boolean
  treeClassName?: string
}

export default function WorkspaceFolderTree<T extends TreeId = TreeId>({
  rootTitle,
  folders,
  leaves,
  expandedKeys,
  onExpandedKeysChange,
  selectedLeafId,
  onSelectLeaf,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onRenameLeaf,
  onDeleteLeaf,
  onCopyLeaf,
  onMoveAndReorder,
  onMoveFolder,
  onReorderFolders,
  folderMenuExtra,
  readOnly,
  showRootCreateButton = true,
  treeClassName = 'workspace-folder-tree',
}: Props<T>) {
  const [renamingFolderId, setRenamingFolderId] = useState<T | null>(null)
  const [renamingLeafId, setRenamingLeafId] = useState<T | null>(null)
  const dragPointerRef = useRef<DragPointer | null>(null)
  // treeData 在 useMemo 里缓存了 onBlur/onPressEnter；必须用 ref 读最新输入，否则提交的是进入重命名时的旧名
  const renamingFolderNameRef = useRef('')
  const renamingLeafNameRef = useRef('')
  const renamingFolderIdRef = useRef<T | null>(null)
  const renamingLeafIdRef = useRef<T | null>(null)

  const beginRenameFolder = (id: T, name: string) => {
    renamingFolderIdRef.current = id
    renamingFolderNameRef.current = name
    setRenamingFolderId(id)
  }

  const beginRenameLeaf = (id: T, name: string) => {
    renamingLeafIdRef.current = id
    renamingLeafNameRef.current = name
    setRenamingLeafId(id)
  }

  const rememberDragPointer = (info: { event: any; node: { key?: React.Key } }) => {
    const ev = info.event as { clientX?: number; clientY?: number; altKey?: boolean } | undefined
    if (ev?.clientX == null || ev?.clientY == null) return
    dragPointerRef.current = {
      hoverKey: String(info.node.key),
      clientX: ev.clientX,
      clientY: ev.clientY,
      altKey: Boolean(ev.altKey),
    }
  }

  const sortLeaves = (list: LeafRow<T>[]) => sortLeavesByOrderThenName(list)
  const sortFolders = (list: FolderRow<T>[]) => sortFoldersByOrderThenName(list)

  const commitRenameFolder = async (id: T) => {
    if (!sameId(renamingFolderIdRef.current, id)) return
    const name = renamingFolderNameRef.current.trim()
    renamingFolderIdRef.current = null
    setRenamingFolderId(null)
    if (!name) return
    const cur = folders.find(f => sameId(f.id, id))
    if (cur && cur.name === name) return
    try {
      await onRenameFolder(id, name)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重命名失败')
    }
  }

  const commitRenameLeaf = async (id: T) => {
    if (!sameId(renamingLeafIdRef.current, id)) return
    const name = renamingLeafNameRef.current.trim()
    renamingLeafIdRef.current = null
    setRenamingLeafId(null)
    if (!name) return
    const cur = leaves.find(l => sameId(l.id, id))
    if (cur && cur.name === name) return
    try {
      await onRenameLeaf(id, name)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重命名失败')
    }
  }

  const treeData = useMemo(() => {
    const folderMap: Record<string, any> = {}
    folders.forEach(f => {
      const fid = String(f.id)
      folderMap[fid] = {
        key: `folder-${fid}`,
        title: sameId(renamingFolderId, f.id) ? (
          <Input
            size="small"
            autoFocus
            defaultValue={f.name}
            style={{ width: 120 }}
            onChange={e => { renamingFolderNameRef.current = e.target.value }}
            onPressEnter={() => void commitRenameFolder(f.id)}
            onBlur={() => void commitRenameFolder(f.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            data-tree-key={`folder-${fid}`}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={readOnly ? undefined : () => beginRenameFolder(f.id, f.name)}
          >
            <span><FolderOutlined style={{ marginRight: 6, color: '#faad14' }} />{f.name}</span>
            {!readOnly && <Dropdown
              menu={{
                items: [
                  ...(folderMenuExtra?.(f) || []),
                  { key: 'add-folder', label: '新建子目录', onClick: () => onCreateFolder(f.id) },
                  ...(onMoveFolder && f.parent_id != null ? [
                    {
                      key: 'move-up',
                      label: '移到上一级',
                      onClick: () => {
                        const parent = folders.find(x => sameId(x.id, f.parent_id))
                        const targetParentId = (parent?.parent_id ?? null) as T | null
                        void onMoveFolder({ folderId: f.id, targetParentId })
                          .then(() => message.success('已移到上一级'))
                          .catch((e: any) => message.error(e?.response?.data?.detail || '移动失败'))
                      },
                    },
                    {
                      key: 'move-root',
                      label: '移到根目录',
                      onClick: () => {
                        void onMoveFolder({ folderId: f.id, targetParentId: null })
                          .then(() => message.success('已移到根目录'))
                          .catch((e: any) => message.error(e?.response?.data?.detail || '移动失败'))
                      },
                    },
                  ] : []),
                  { key: 'rename', label: '重命名', onClick: () => beginRenameFolder(f.id, f.name) },
                  {
                    key: 'delete',
                    label: <span style={{ color: 'red' }}>删除目录</span>,
                    onClick: () => { void onDeleteFolder(f.id).catch((e: any) => message.error(e?.response?.data?.detail || '删除失败')) },
                  },
                ],
              }}
              trigger={['click']}
            >
              <MoreOutlined style={{ padding: '0 4px', color: '#999' }} onClick={e => e.stopPropagation()} />
            </Dropdown>}
          </div>
        ),
        children: [] as any[],
        isLeaf: false,
        _folderId: f.id,
        _parentId: f.parent_id,
        _name: f.name,
        _sortOrder: f.sort_order ?? 0,
      }
    })

    const rootLeaves: any[] = []
    sortLeaves(leaves).forEach(n => {
      const leafItem = {
        key: String(n.id),
        title: sameId(renamingLeafId, n.id) ? (
          <Input
            size="small"
            autoFocus
            defaultValue={n.name}
            style={{ width: 130 }}
            onChange={e => { renamingLeafNameRef.current = e.target.value }}
            onPressEnter={() => void commitRenameLeaf(n.id)}
            onBlur={() => void commitRenameLeaf(n.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            data-tree-key={String(n.id)}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={readOnly ? undefined : () => beginRenameLeaf(n.id, n.name)}
          >
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <FileOutlined style={{ marginRight: 6, color: n.job_type === 'JAR' ? '#fa8c16' : '#1677ff' }} />
              {n.name}
            </span>
            {!readOnly && <Dropdown
              menu={{
                items: [
                  { key: 'open', label: '打开', onClick: () => onSelectLeaf(n) },
                  { key: 'rename', label: '重命名', onClick: () => beginRenameLeaf(n.id, n.name) },
                  ...(onCopyLeaf ? [{ key: 'copy', label: '复制', onClick: () => onCopyLeaf(n) }] : []),
                  { key: 'delete', label: <span style={{ color: 'red' }}>删除</span>, onClick: () => onDeleteLeaf(n) },
                ],
              }}
              trigger={['click']}
            >
              <MoreOutlined style={{ padding: '0 4px', color: '#999' }} onClick={e => e.stopPropagation()} />
            </Dropdown>}
          </div>
        ),
        isLeaf: true,
        data: n,
      }
      const fk = n.folder_id != null ? String(n.folder_id) : ''
      if (fk && folderMap[fk]) {
        folderMap[fk].children.push(leafItem)
      } else {
        rootLeaves.push(leafItem)
      }
    })

    const rootFolders: any[] = []
    Object.values(folderMap).forEach((f: any) => {
      const leafChildren = f.children.filter((c: any) => c.isLeaf)
      const subFolders = f.children.filter((c: any) => !c.isLeaf)
      leafChildren.sort((a: any, b: any) => {
        const so = (a.data?.sort_order ?? 0) - (b.data?.sort_order ?? 0)
        if (so !== 0) return so
        const nc = String(a.data?.name || '').localeCompare(String(b.data?.name || ''), 'zh-CN', {
          numeric: true,
          sensitivity: 'base',
        })
        if (nc !== 0) return nc
        return compareIdTie(a.data?.id ?? 0, b.data?.id ?? 0)
      })
      subFolders.sort((a: any, b: any) => {
        const so = (a._sortOrder ?? 0) - (b._sortOrder ?? 0)
        if (so !== 0) return so
        const nc = String(a._name || '').localeCompare(String(b._name || ''), 'zh-CN', {
          numeric: true,
          sensitivity: 'base',
        })
        if (nc !== 0) return nc
        return compareIdTie(a._folderId ?? 0, b._folderId ?? 0)
      })
      f.children = [...subFolders, ...leafChildren]
      const pid = f._parentId != null ? String(f._parentId) : ''
      if (pid && folderMap[pid]) {
        folderMap[pid].children.push(f)
      } else {
        rootFolders.push(f)
      }
    })

    rootFolders.sort((a: any, b: any) => {
      const so = (a._sortOrder ?? 0) - (b._sortOrder ?? 0)
      if (so !== 0) return so
      const nc = String(a._name || '').localeCompare(String(b._name || ''), 'zh-CN', {
        numeric: true,
        sensitivity: 'base',
      })
      if (nc !== 0) return nc
      return compareIdTie(a._folderId ?? 0, b._folderId ?? 0)
    })

    return [
      {
        key: 'root',
        title: (
          <span style={{ fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
            <span><FolderOutlined style={{ marginRight: 6 }} />{rootTitle}</span>
            {!readOnly && showRootCreateButton && (
              <Button type="link" size="small" onClick={(e) => { e.stopPropagation(); onCreateFolder(null) }}>新建目录</Button>
            )}
          </span>
        ),
        children: [...rootFolders, ...rootLeaves],
      },
    ] as DataNode[]
  }, [
    folders, leaves, renamingFolderId, renamingLeafId, rootTitle,
    folderMenuExtra, onCreateFolder, onDeleteFolder, onDeleteLeaf, onCopyLeaf, onSelectLeaf, readOnly,
    showRootCreateButton, onMoveFolder,
  ])

  const onDrop: TreeProps['onDrop'] = async (info) => {
    const dragKey = String(info.dragNode.key)
    const pointer = dragPointerRef.current
    dragPointerRef.current = null

    if (dragKey.startsWith('folder-')) {
      if (!onMoveFolder && !onReorderFolders) {
        message.info('暂不支持拖拽目录')
        return
      }
      const rawFolder = parseFolderKey(dragKey)
      if (rawFolder == null) return
      const folderId = folderKeyToId(rawFolder, folders)
      if (folderId == null) return
      const cur = folders.find(f => sameId(f.id, folderId))
      if (!cur) return

      const ev = info.event as unknown as { clientX?: number; clientY?: number; altKey?: boolean }
      const clientX = pointer?.clientX ?? ev?.clientX
      const clientY = pointer?.clientY ?? ev?.clientY
      const altKey = Boolean(pointer?.altKey || ev?.altKey)
      const pointKey = clientX != null && clientY != null
        ? treeKeyFromPoint(clientX, clientY, dragKey)
        : null
      // rc-tree 会改写 info.node；同级重排必须用真实悬停行
      const dropKey = pickVisualDropKey({
        hoverKey: pointer?.hoverKey,
        pointKey,
        antdDropKey: String(info.node.key),
        dragKey,
      })
      const dropLeafFolderId = dropKey.startsWith('folder-') || dropKey === 'root'
        ? undefined
        : (leaves.find(l => sameId(l.id, leafKeyToId(dropKey, leaves)))?.folder_id ?? null)
      const dropNodeRect = rectForTreeKey(dropKey)

      const intent = resolveFolderDropIntent({
        draggedId: folderId,
        draggedParentId: (cur.parent_id ?? null) as T | null,
        dropKey,
        dropToGap: Boolean(info.dropToGap),
        dropPosition: info.dropPosition,
        nodePos: String(info.node.pos || ''),
        folders,
        dropLeafFolderId,
        nestModifier: altKey,
        clientY,
        dropNodeRect,
      })
      if (!intent) return

      try {
        if (intent.kind === 'reparent') {
          if (!onMoveFolder) {
            message.info('暂不支持移动目录到其他父级')
            return
          }
          if (sameId(cur.parent_id, intent.targetParentId)) {
            message.info('顺序未变化：请拖到目标行的上半（提到前面）或下半（放到后面）；按住 Alt 拖到目录可迁入')
            return
          }
          await onMoveFolder({ folderId, targetParentId: intent.targetParentId })
          message.success('目录已移动')
          return
        }

        const targetParentId = intent.parentId
        const needsReparent = folderReorderNeedsReparent((cur.parent_id ?? null) as T | null, intent)
        if (needsReparent) {
          if (!onMoveFolder) {
            message.info('暂不支持移动目录到其他父级')
            return
          }
          await onMoveFolder({ folderId, targetParentId })
        }
        if (!onReorderFolders) {
          if (needsReparent) message.success('目录已移动')
          else message.info('暂不支持目录排序')
          return
        }
        const peersExcl = sortFolders(
          folders.filter(f => sameId(f.parent_id, targetParentId) && !sameId(f.id, folderId)),
        ).map(f => f.id)
        const orderedIds = insertAmongPeers({
          peerIdsExcludingDragged: peersExcl,
          draggedId: folderId,
          relativeId: intent.relativeId,
          position: intent.position,
          insertIndex: intent.insertIndex,
        })
        const prevAtTarget = sortFolders(
          folders.filter(f => sameId(f.parent_id, targetParentId)),
        ).map(f => f.id)
        const orderUnchanged = !needsReparent
          && orderedIds.map(String).join(',') === prevAtTarget.map(String).join(',')
        if (orderUnchanged) {
          message.info('顺序未变化：请拖到目标行的上半（提到前面）或下半（放到后面）；按住 Alt 拖到目录可迁入')
          return
        }
        await onReorderFolders({ parentId: targetParentId, orderedFolderIds: orderedIds })
        message.success(needsReparent ? '目录已移出并更新顺序' : '目录顺序已更新')
      } catch (e: any) {
        message.error(e?.response?.data?.detail || '目录移动失败')
      }
      return
    }

    if (dragKey === 'root') return

    const leafId = leafKeyToId(dragKey, leaves)
    if (leafId == null) return

    const ev = info.event as unknown as { clientX?: number; clientY?: number }
    const clientX = pointer?.clientX ?? ev?.clientX
    const clientY = pointer?.clientY ?? ev?.clientY
    const pointKey = clientX != null && clientY != null
      ? treeKeyFromPoint(clientX, clientY, dragKey)
      : null
    const dropKey = pickVisualDropKey({
      hoverKey: pointer?.hoverKey,
      pointKey,
      antdDropKey: String(info.node.key),
      dragKey,
    })
    let targetFolderId: T | null = null
    let ordered: T[]

    if (dropKey.startsWith('folder-')) {
      const dropRaw = parseFolderKey(dropKey)
      targetFolderId = dropRaw != null ? folderKeyToId(dropRaw, folders) : null
      const inFolder = sortLeaves(
        leaves.filter(l => sameId(l.folder_id, targetFolderId) && !sameId(l.id, leafId)),
      )
      // 拖到目录上：迁入该目录末尾（与资源管理器「放进文件夹」一致）
      ordered = [...inFolder.map(l => l.id), leafId]
    } else if (dropKey === 'root') {
      targetFolderId = null
      const inFolder = sortLeaves(
        leaves.filter(l => sameId(l.folder_id, null) && !sameId(l.id, leafId)),
      )
      ordered = [...inFolder.map(l => l.id), leafId]
    } else {
      const dropLeafId = leafKeyToId(dropKey, leaves)
      const dropLeaf = leaves.find(l => sameId(l.id, dropLeafId))
      targetFolderId = (dropLeaf?.folder_id ?? null) as T | null
      const inFolder = sortLeaves(
        leaves.filter(l => sameId(l.folder_id, targetFolderId) && !sameId(l.id, leafId)),
      )
      const dropNodeRect = rectForTreeKey(dropKey)
      const fromPointer = positionByPointerHalf(clientY, dropNodeRect)
      let position: 'before' | 'after' = fromPointer ?? 'before'
      if (!fromPointer && Boolean(info.dropToGap)) {
        const parts = String(info.node.pos || '').split('-')
        const nodeIndex = Number(parts[parts.length - 1] || 0)
        if (info.dropPosition - nodeIndex !== -1) position = 'after'
      }
      // 同级脚本：与目录相同，上半前 / 下半后
      ordered = insertAmongPeers({
        peerIdsExcludingDragged: inFolder.map(l => l.id),
        draggedId: leafId,
        relativeId: dropLeafId,
        position,
      })
    }
    const leaf = leaves.find(l => sameId(l.id, leafId))
    const folderChanged = !sameId(leaf?.folder_id, targetFolderId)
    try {
      await onMoveAndReorder({ leafId, targetFolderId, orderedLeafIds: ordered, folderChanged })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '移动失败')
    }
  }

  return (
    <Tree
      className={treeClassName}
      blockNode
      draggable={!readOnly}
      allowDrop={({ dropNode, dragNode }) => {
        const dragKey = String(dragNode.key)
        const dropKey = String(dropNode.key)
        if (dragKey === 'root') return false
        if (dragKey.startsWith('folder-')) {
          if (!onMoveFolder && !onReorderFolders) return false
          if (dropKey === dragKey) return false
          return true
        }
        return true
      }}
      treeData={treeData}
      expandedKeys={expandedKeys}
      onExpand={keys => onExpandedKeysChange(keys)}
      selectedKeys={selectedLeafId != null ? [String(selectedLeafId)] : []}
      onSelect={(keys) => {
        const k = String(keys[0] || '')
        if (!k || k === 'root' || k.startsWith('folder-')) return
        const leaf = leaves.find(l => String(l.id) === k)
        if (leaf) onSelectLeaf(leaf)
      }}
      onDragEnter={rememberDragPointer}
      onDragOver={rememberDragPointer}
      onDrop={onDrop}
      style={{ padding: '4px 0' }}
    />
  )
}

/** 展开祖先目录并滚动到选中叶节点 */
export function locateLeafInFolderTree<T extends TreeId = TreeId>(opts: {
  leafId: T
  leaves: LeafRow<T>[]
  folders: FolderRow<T>[]
  expandedKeys: React.Key[]
  setExpandedKeys: (keys: React.Key[]) => void
  treeSelector?: string
}) {
  const { leafId, leaves, folders, expandedKeys, setExpandedKeys, treeSelector = '.workspace-folder-tree' } = opts
  const leaf = leaves.find(l => sameId(l.id, leafId))
  if (!leaf) return
  const keys = new Set<React.Key>([
    ...expandedKeys,
    ...ancestorFolderKeys({ leafFolderId: leaf.folder_id, folders }),
  ])
  setExpandedKeys([...keys])
  window.setTimeout(() => {
    const el = document.querySelector(`${treeSelector} .ant-tree-node-selected`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, 80)
}

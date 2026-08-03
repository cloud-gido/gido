/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useMemo, useState } from 'react'
import { Button, Dropdown, Input, Tree, message } from 'antd'
import type { DataNode, TreeProps } from 'antd/es/tree'
import { FileOutlined, FolderOutlined, MoreOutlined } from '@ant-design/icons'
import { sortLeavesByOrderThenName, sortFoldersByOrderThenName } from '../utils/treeSort'
import { ancestorFolderKeys, folderReorderNeedsReparent, insertAmongPeers, orderLeavesAfterDrop, reorderPeerIdsByDrop, resolveFolderDropIntent } from '../utils/treeDropOrder'

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
  const [renamingFolderName, setRenamingFolderName] = useState('')
  const [renamingLeafId, setRenamingLeafId] = useState<T | null>(null)
  const [renamingLeafName, setRenamingLeafName] = useState('')

  const sortLeaves = (list: LeafRow<T>[]) => sortLeavesByOrderThenName(list)
  const sortFolders = (list: FolderRow<T>[]) => sortFoldersByOrderThenName(list)

  const commitRenameFolder = async (id: T) => {
    const name = renamingFolderName.trim()
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
    const name = renamingLeafName.trim()
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
            onChange={e => setRenamingFolderName(e.target.value)}
            onPressEnter={() => void commitRenameFolder(f.id)}
            onBlur={() => void commitRenameFolder(f.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={readOnly ? undefined : () => { setRenamingFolderId(f.id); setRenamingFolderName(f.name) }}
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
                  { key: 'rename', label: '重命名', onClick: () => { setRenamingFolderId(f.id); setRenamingFolderName(f.name) } },
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
            onChange={e => setRenamingLeafName(e.target.value)}
            onPressEnter={() => void commitRenameLeaf(n.id)}
            onBlur={() => void commitRenameLeaf(n.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={readOnly ? undefined : () => { setRenamingLeafId(n.id); setRenamingLeafName(n.name) }}
          >
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <FileOutlined style={{ marginRight: 6, color: n.job_type === 'JAR' ? '#fa8c16' : '#1677ff' }} />
              {n.name}
            </span>
            {!readOnly && <Dropdown
              menu={{
                items: [
                  { key: 'open', label: '打开', onClick: () => onSelectLeaf(n) },
                  { key: 'rename', label: '重命名', onClick: () => { setRenamingLeafId(n.id); setRenamingLeafName(n.name) } },
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

      const dropKey = String(info.node.key)
      const dropLeafFolderId = dropKey.startsWith('folder-') || dropKey === 'root'
        ? undefined
        : (leaves.find(l => sameId(l.id, leafKeyToId(dropKey, leaves)))?.folder_id ?? null)
      const dropFolderRaw = dropKey.startsWith('folder-') ? parseFolderKey(dropKey) : null
      const dropFolderExpanded = dropFolderRaw != null
        && expandedKeys.map(String).includes(`folder-${dropFolderRaw}`)

      const intent = resolveFolderDropIntent({
        draggedId: folderId,
        draggedParentId: (cur.parent_id ?? null) as T | null,
        dropKey,
        dropToGap: Boolean(info.dropToGap),
        dropPosition: info.dropPosition,
        nodePos: String(info.node.pos || ''),
        folders,
        dropLeafFolderId,
        dropFolderExpanded,
      })
      if (!intent) return

      try {
        if (intent.kind === 'reparent') {
          if (!onMoveFolder) {
            message.info('暂不支持移动目录到其他父级')
            return
          }
          if (sameId(cur.parent_id, intent.targetParentId)) return
          await onMoveFolder({ folderId, targetParentId: intent.targetParentId })
          message.success('目录已移动')
          return
        }

        // reorder：目标父级可能与当前不同（子目录拖到根/父目录旁 → 先提出再排序）
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

        const peerIds = sortFolders(
          folders.filter(f => sameId(f.parent_id, targetParentId)),
        ).map(f => f.id)

        let orderedIds: T[]
        if (intent.relativeId != null && !needsReparent) {
          const dropPosParts = String(info.node.pos || '').split('-')
          const nodeIndex = Number(dropPosParts[dropPosParts.length - 1] || 0)
          orderedIds = reorderPeerIdsByDrop({
            peerIdsInDisplayOrder: peerIds,
            draggedId: folderId,
            dropId: intent.relativeId,
            relativeDrop: info.dropPosition - nodeIndex,
            dropToGap: Boolean(info.dropToGap),
          })
        } else {
          const peersExcl = peerIds.filter(id => !sameId(id, folderId))
          orderedIds = insertAmongPeers({
            peerIdsExcludingDragged: peersExcl,
            draggedId: folderId,
            relativeId: intent.relativeId,
            position: intent.position,
            insertIndex: intent.insertIndex,
          })
        }

        const prevAtTarget = peerIds
        const orderUnchanged = !needsReparent
          && orderedIds.map(String).join(',') === prevAtTarget.map(String).join(',')
        if (orderUnchanged) {
          message.info('顺序未变化：请拖到目标目录上，或拖到其上方空隙')
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

    const dropKey = String(info.node.key)
    let targetFolderId: T | null = null

    if (dropKey.startsWith('folder-')) {
      const dropRaw = parseFolderKey(dropKey)
      targetFolderId = dropRaw != null ? folderKeyToId(dropRaw, folders) : null
    } else if (dropKey === 'root') {
      targetFolderId = null
    } else {
      const dropLeaf = leaves.find(l => sameId(l.id, leafKeyToId(dropKey, leaves)))
      targetFolderId = (dropLeaf?.folder_id ?? null) as T | null
    }

    const inFolder = sortLeaves(
      leaves.filter(l => sameId(l.folder_id, targetFolderId) && !sameId(l.id, leafId)),
    )
    const dropLeafId = dropKey.startsWith('folder-') || dropKey === 'root'
      ? null
      : leafKeyToId(dropKey, leaves)
    const ordered = orderLeavesAfterDrop({
      peerIdsExcludingDragged: inFolder.map(l => l.id),
      draggedId: leafId,
      dropRelativeLeafId: dropLeafId,
      dropToGap: Boolean(info.dropToGap),
      dropPositionHint: info.dropPosition,
    })
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

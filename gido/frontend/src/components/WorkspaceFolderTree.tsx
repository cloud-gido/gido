/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React, { useMemo, useRef, useState } from 'react'
import { Button, Dropdown, Input, Tree, message } from 'antd'
import type { DataNode, TreeProps } from 'antd/es/tree'
import { FileOutlined, FolderOutlined, MoreOutlined } from '@ant-design/icons'
import { buildSortedWorkspaceTree, sortByName } from '../utils/treeSort'
import {
  ancestorFolderKeys,
  pickVisualDropKey,
  resolveFolderMoveIntent,
  resolveLeafMoveTarget,
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

type DragPointer = {
  hoverKey: string
  clientX: number
  clientY: number
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
  /** 叶子换目录；orderedLeafIds 为迁入后按名称排好的同级 id（便于后端 sync，UI 本身不按手工序） */
  onMoveAndReorder: (opts: {
    leafId: T
    targetFolderId: T | null
    orderedLeafIds: T[]
    folderChanged: boolean
  }) => Promise<void>
  /** 目录换父级（迁入 / 移出）；同级不排序 */
  onMoveFolder?: (opts: { folderId: T; targetParentId: T | null }) => Promise<void>
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
    const ev = info.event as { clientX?: number; clientY?: number } | undefined
    if (ev?.clientX == null || ev?.clientY == null) return
    dragPointerRef.current = {
      hoverKey: String(info.node.key),
      clientX: ev.clientX,
      clientY: ev.clientY,
    }
  }

  const sortNamed = <R extends { id?: TreeId; name?: string }>(list: R[]) => sortByName(list)

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
      }
    })

    const leafMap: Record<string, any> = {}
    leaves.forEach(n => {
      leafMap[String(n.id)] = {
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
    })

    // 结构与每一层排序由共享纯函数保证（Studio / Probe / Stream 同一路径）
    const hierarchy = buildSortedWorkspaceTree({ folders, leaves })
    const toDataNodes = (nodes: ReturnType<typeof buildSortedWorkspaceTree<T>>): any[] =>
      nodes.map(n => {
        if (n.kind === 'folder') {
          const fo = folderMap[String(n.id)]
          return { ...fo, children: toDataNodes(n.children) }
        }
        return leafMap[String(n.id)]
      }).filter(Boolean)

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
        children: toDataNodes(hierarchy),
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
    const sameLevelHint = '同级按「目录在前、脚本在后」字典序排列，不支持手动调序；请拖到目标目录上迁入，或拖到根/其它层级以移动'

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
    // 悬停在真实目录行上时，优先按「迁入」处理（避免 rc-tree 缝隙改写）
    const dropToGap = dropKey.startsWith('folder-') && dropKey === pointer?.hoverKey
      ? false
      : Boolean(info.dropToGap)

    if (dragKey.startsWith('folder-')) {
      if (!onMoveFolder) {
        message.info('暂不支持拖拽目录')
        return
      }
      const rawFolder = parseFolderKey(dragKey)
      if (rawFolder == null) return
      const folderId = folderKeyToId(rawFolder, folders)
      if (folderId == null) return
      const cur = folders.find(f => sameId(f.id, folderId))
      if (!cur) return

      const dropLeafFolderId = dropKey.startsWith('folder-') || dropKey === 'root'
        ? undefined
        : (leaves.find(l => sameId(l.id, leafKeyToId(dropKey, leaves)))?.folder_id ?? null)

      const intent = resolveFolderMoveIntent({
        draggedId: folderId,
        draggedParentId: (cur.parent_id ?? null) as T | null,
        dropKey,
        dropToGap,
        folders,
        dropLeafFolderId,
      })
      if (!intent) {
        message.info(sameLevelHint)
        return
      }
      try {
        if (sameId(cur.parent_id, intent.targetParentId)) {
          message.info(sameLevelHint)
          return
        }
        await onMoveFolder({ folderId, targetParentId: intent.targetParentId })
        message.success('目录已移动')
      } catch (e: any) {
        message.error(e?.response?.data?.detail || '目录移动失败')
      }
      return
    }

    if (dragKey === 'root') return

    const leafId = leafKeyToId(dragKey, leaves)
    if (leafId == null) return
    const leaf = leaves.find(l => sameId(l.id, leafId))
    if (!leaf) return

    const dropLeafFolderId = dropKey.startsWith('folder-') || dropKey === 'root'
      ? undefined
      : (leaves.find(l => sameId(l.id, leafKeyToId(dropKey, leaves)))?.folder_id ?? null)

    const target = resolveLeafMoveTarget({
      draggedFolderId: (leaf.folder_id ?? null) as T | null,
      dropKey,
      dropToGap,
      folders,
      dropLeafFolderId,
    })
    if (target === undefined) {
      message.info(sameLevelHint)
      return
    }
    const targetFolderId = target
    const folderChanged = !sameId(leaf.folder_id, targetFolderId)
    if (!folderChanged) {
      message.info(sameLevelHint)
      return
    }
    const orderedLeafIds = sortNamed([
      ...leaves.filter(l => sameId(l.folder_id, targetFolderId) && !sameId(l.id, leafId)),
      { ...leaf, folder_id: targetFolderId, name: leaf.name, id: leaf.id },
    ]).map(l => l.id as T)

    try {
      await onMoveAndReorder({ leafId, targetFolderId, orderedLeafIds, folderChanged: true })
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
          if (!onMoveFolder) return false
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

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 工作空间目录树（批 Studio / 实时 Stream 共用交互模式）。
 */
import { Button, Dropdown, Input, Tree, message } from 'antd'
import type { DataNode, TreeProps } from 'antd/es/tree'
import {
  FileOutlined,
  FolderOutlined,
  MoreOutlined,
} from '@ant-design/icons'
import { useMemo, useState } from 'react'

export type FolderRow = { id: number; name: string; parent_id: number | null }
export type LeafRow = {
  id: number
  name: string
  folder_id?: number | null
  sort_order?: number | null
  job_type?: string
  [key: string]: any
}

type Props = {
  rootTitle: string
  treeClassName?: string
  folders: FolderRow[]
  leaves: LeafRow[]
  selectedLeafId?: number | null
  expandedKeys: React.Key[]
  onExpandedKeysChange: (keys: React.Key[]) => void
  onSelectLeaf: (leaf: LeafRow) => void
  onCreateFolder: (parentId: number | null) => void
  onRenameFolder: (id: number, name: string) => Promise<void>
  onDeleteFolder: (id: number) => Promise<void>
  onRenameLeaf: (id: number, name: string) => Promise<void>
  onDeleteLeaf: (leaf: LeafRow) => void
  onCopyLeaf?: (leaf: LeafRow) => void
  onMoveAndReorder: (args: {
    leafId: number
    targetFolderId: number | null
    orderedLeafIds: number[]
    folderChanged: boolean
  }) => Promise<void>
  folderMenuExtra?: (folder: FolderRow) => { key: string; label: React.ReactNode; onClick?: () => void }[]
  readOnly?: boolean
}

function sortLeaves(list: LeafRow[]) {
  return [...list].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
  )
}

export default function WorkspaceFolderTree({
  rootTitle,
  treeClassName = 'workspace-folder-tree',
  folders,
  leaves,
  selectedLeafId,
  expandedKeys,
  onExpandedKeysChange,
  onSelectLeaf,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onRenameLeaf,
  onDeleteLeaf,
  onCopyLeaf,
  onMoveAndReorder,
  folderMenuExtra,
  readOnly = false,
}: Props) {
  const [renamingFolderId, setRenamingFolderId] = useState<number | null>(null)
  const [renamingFolderName, setRenamingFolderName] = useState('')
  const [renamingLeafId, setRenamingLeafId] = useState<number | null>(null)
  const [renamingLeafName, setRenamingLeafName] = useState('')

  const commitRenameFolder = async (id: number) => {
    const name = renamingFolderName.trim()
    setRenamingFolderId(null)
    if (!name) return
    const cur = folders.find(f => f.id === id)
    if (cur && cur.name === name) return
    try {
      await onRenameFolder(id, name)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重命名失败')
    }
  }

  const commitRenameLeaf = async (id: number) => {
    const name = renamingLeafName.trim()
    setRenamingLeafId(null)
    if (!name) return
    const cur = leaves.find(l => l.id === id)
    if (cur && cur.name === name) return
    try {
      await onRenameLeaf(id, name)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重命名失败')
    }
  }

  const treeData = useMemo(() => {
    const folderMap: Record<number, any> = {}
    folders.forEach(f => {
      folderMap[f.id] = {
        key: `folder-${f.id}`,
        title: renamingFolderId === f.id ? (
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
      }
    })

    const rootLeaves: any[] = []
    sortLeaves(leaves).forEach(n => {
      const leafItem = {
        key: String(n.id),
        title: renamingLeafId === n.id ? (
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
      if (n.folder_id && folderMap[n.folder_id]) {
        folderMap[n.folder_id].children.push(leafItem)
      } else {
        rootLeaves.push(leafItem)
      }
    })

    const rootFolders: any[] = []
    Object.values(folderMap).forEach((f: any) => {
      const leafChildren = f.children.filter((c: any) => c.isLeaf)
      const subFolders = f.children.filter((c: any) => !c.isLeaf)
      leafChildren.sort(
        (a: any, b: any) => (a.data?.sort_order ?? 0) - (b.data?.sort_order ?? 0) || a.data.id - b.data.id,
      )
      f.children = [...subFolders, ...leafChildren]
      if (f._parentId && folderMap[f._parentId]) {
        folderMap[f._parentId].children.unshift(f)
      } else {
        rootFolders.push(f)
      }
    })

    return [
      {
        key: 'root',
        title: (
          <span style={{ fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
            <span><FolderOutlined style={{ marginRight: 6 }} />{rootTitle}</span>
            {!readOnly && <Button type="link" size="small" onClick={(e) => { e.stopPropagation(); onCreateFolder(null) }}>新建目录</Button>}
          </span>
        ),
        children: [...rootFolders, ...rootLeaves],
      },
    ] as DataNode[]
  }, [
    folders, leaves, renamingFolderId, renamingLeafId, rootTitle,
    folderMenuExtra, onCreateFolder, onDeleteFolder, onDeleteLeaf, onCopyLeaf, onSelectLeaf, readOnly,
  ])

  const onDrop: TreeProps['onDrop'] = async (info) => {
    const dragKey = String(info.dragNode.key)
    if (dragKey.startsWith('folder-') || dragKey === 'root') {
      message.info('暂不支持拖拽目录，请拖拽作业')
      return
    }
    const leafId = Number(dragKey)
    if (!Number.isFinite(leafId)) return

    const dropKey = String(info.node.key)
    let targetFolderId: number | null = null

    if (dropKey.startsWith('folder-')) {
      targetFolderId = Number(dropKey.replace('folder-', ''))
    } else if (dropKey === 'root') {
      targetFolderId = null
    } else {
      const dropLeaf = leaves.find(l => l.id === Number(dropKey))
      targetFolderId = dropLeaf?.folder_id ?? null
    }

    // rebuild ordered ids among leaves in target folder after drop
    const inFolder = sortLeaves(leaves.filter(l => (l.folder_id ?? null) === targetFolderId && l.id !== leafId))
    const dropLeafId = dropKey.startsWith('folder-') || dropKey === 'root' ? null : Number(dropKey)
    let ordered = inFolder.map(l => l.id)
    if (dropLeafId && Number.isFinite(dropLeafId)) {
      const idx = ordered.indexOf(dropLeafId)
      if (idx >= 0) ordered.splice(info.dropPosition > idx ? idx + 1 : idx, 0, leafId)
      else ordered.push(leafId)
    } else {
      ordered = info.dropToGap ? [...ordered, leafId] : [leafId, ...ordered]
    }
    const leaf = leaves.find(l => l.id === leafId)
    const folderChanged = (leaf?.folder_id ?? null) !== targetFolderId
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
        if (dragKey.startsWith('folder-') || dragKey === 'root') return false
        return true
      }}
      treeData={treeData}
      expandedKeys={expandedKeys}
      onExpand={keys => onExpandedKeysChange(keys)}
      selectedKeys={selectedLeafId != null ? [String(selectedLeafId)] : []}
      onSelect={(keys, info) => {
        const k = String(keys[0] || '')
        if (!k || k === 'root' || k.startsWith('folder-')) return
        const leaf = leaves.find(l => l.id === Number(k))
        if (leaf) onSelectLeaf(leaf)
      }}
      onDrop={onDrop}
      style={{ padding: '4px 0' }}
    />
  )
}

/** 展开祖先目录并滚动到选中叶节点 */
export function locateLeafInFolderTree(opts: {
  leafId: number
  leaves: LeafRow[]
  folders: FolderRow[]
  expandedKeys: React.Key[]
  setExpandedKeys: (keys: React.Key[]) => void
  treeSelector?: string
}) {
  const { leafId, leaves, folders, expandedKeys, setExpandedKeys, treeSelector = '.workspace-folder-tree' } = opts
  const leaf = leaves.find(l => l.id === leafId)
  if (!leaf) return
  const keys = new Set<React.Key>([...expandedKeys, 'root'])
  let fid = leaf.folder_id ?? null
  while (fid != null) {
    keys.add(`folder-${fid}`)
    const f = folders.find(x => x.id === fid)
    fid = f?.parent_id ?? null
  }
  setExpandedKeys([...keys])
  window.setTimeout(() => {
    const el = document.querySelector(`${treeSelector} .ant-tree-node-selected`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, 80)
}

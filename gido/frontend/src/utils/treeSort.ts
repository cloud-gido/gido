/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树排序（对齐常见操作系统 / IDEA 资源管理器）：
 * 同级固定「目录在前、脚本在后」，组内按名称字典序；不支持手工拖拽排序。
 * 每一层（根与任意嵌套目录）必须同一规则——由 buildSortedWorkspaceTree 统一保证。
 */

function cmpId(a: string | number | null | undefined, b: string | number | null | undefined): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isFinite(na) && Number.isFinite(nb) && String(a) === String(na) && String(b) === String(nb)) {
    return na - nb
  }
  return String(a ?? '').localeCompare(String(b ?? ''), 'zh-CN', { numeric: true, sensitivity: 'base' })
}

export function cmpZhName(a: string, b: string): number {
  return String(a || '').localeCompare(String(b || ''), 'zh-CN', {
    numeric: true,
    sensitivity: 'base',
  })
}

/** 按名称字典序（zh-CN + numeric）；同名再按 id。忽略 sort_order。 */
export function sortByName<
  T extends { id?: string | number; name?: string },
>(list: T[]): T[] {
  return [...list].sort((a, b) => {
    const nc = cmpZhName(a.name || '', b.name || '')
    if (nc !== 0) return nc
    return cmpId(a.id, b.id)
  })
}

export type SortedTreeNode<T extends string | number = string | number> = {
  id: T
  name: string
  kind: 'folder' | 'leaf'
  children: SortedTreeNode<T>[]
}

/**
 * 先挂满父子，再递归每一层排序：目录在前、叶子在后，组内字典序。
 * Studio / Probe / Stream 共用，禁止页面各自再排一遍。
 */
export function buildSortedWorkspaceTree<T extends string | number>(opts: {
  folders: { id: T; name: string; parent_id: T | null }[]
  leaves: { id: T; name: string; folder_id?: T | null }[]
}): SortedTreeNode<T>[] {
  type Internal = SortedTreeNode<T> & { parentId: T | null }
  const folderNodes = new Map<string, Internal>()
  for (const f of opts.folders) {
    folderNodes.set(String(f.id), {
      id: f.id,
      name: f.name,
      kind: 'folder',
      parentId: f.parent_id,
      children: [],
    })
  }

  const rootFolders: Internal[] = []
  const rootLeaves: SortedTreeNode<T>[] = []

  for (const l of opts.leaves) {
    const node: SortedTreeNode<T> = { id: l.id, name: l.name, kind: 'leaf', children: [] }
    const fk = l.folder_id != null ? String(l.folder_id) : ''
    if (fk && folderNodes.has(fk)) folderNodes.get(fk)!.children.push(node)
    else rootLeaves.push(node)
  }

  for (const f of folderNodes.values()) {
    const pid = f.parentId != null ? String(f.parentId) : ''
    if (pid && folderNodes.has(pid)) folderNodes.get(pid)!.children.push(f)
    else rootFolders.push(f)
  }

  const sortLevel = (nodes: SortedTreeNode<T>[]): SortedTreeNode<T>[] => {
    const foldersPart = nodes.filter(n => n.kind === 'folder')
    const leavesPart = nodes.filter(n => n.kind === 'leaf')
    foldersPart.sort((a, b) => {
      const nc = cmpZhName(a.name, b.name)
      return nc !== 0 ? nc : cmpId(a.id, b.id)
    })
    leavesPart.sort((a, b) => {
      const nc = cmpZhName(a.name, b.name)
      return nc !== 0 ? nc : cmpId(a.id, b.id)
    })
    for (const f of foldersPart) {
      f.children = sortLevel(f.children)
    }
    return [...foldersPart, ...leavesPart]
  }

  return sortLevel([...rootFolders, ...rootLeaves])
}

/** 递归校验：每层均为「目录→叶子 + 组内字典序」。返回违规路径文案。 */
export function collectTreeSortViolations(
  nodes: SortedTreeNode[],
  path: string = '',
): string[] {
  const violations: string[] = []
  let seenLeaf = false
  let prevFolderName: string | null = null
  let prevLeafName: string | null = null
  for (const n of nodes) {
    const here = path ? `${path}/${n.name}` : n.name
    if (n.kind === 'folder') {
      if (seenLeaf) violations.push(`${path || '(root)'}: 目录「${n.name}」出现在叶子之后`)
      if (prevFolderName != null && cmpZhName(prevFolderName, n.name) > 0) {
        violations.push(`${path || '(root)'}: 目录顺序错误「${prevFolderName}」应在「${n.name}」之后`)
      }
      prevFolderName = n.name
      violations.push(...collectTreeSortViolations(n.children, here))
    } else {
      seenLeaf = true
      if (prevLeafName != null && cmpZhName(prevLeafName, n.name) > 0) {
        violations.push(`${path || '(root)'}: 叶子顺序错误「${prevLeafName}」应在「${n.name}」之后`)
      }
      prevLeafName = n.name
    }
  }
  return violations
}

/** @deprecated 使用 sortByName；保留别名兼容旧调用 */
export const sortLeavesByOrderThenName = sortByName
/** @deprecated 使用 sortByName */
export const sortFoldersByOrderThenName = sortByName
/** @deprecated 使用 sortByName */
export const sortFoldersByName = sortByName

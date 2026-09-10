/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树按名称过滤（Studio / Probe / Stream 共用）。
 * 仅匹配文件夹与叶子名称子串，不搜脚本正文。
 */
export type FilterFolder<T extends string | number = string | number> = {
  id: T
  name: string
  parent_id: T | null
}

export type FilterLeaf<T extends string | number = string | number> = {
  id: T
  name: string
  folder_id?: T | null
}

function sameId(a: string | number | null | undefined, b: string | number | null | undefined): boolean {
  if (a == null && b == null) return true
  if (a == null || b == null) return false
  return String(a) === String(b)
}

function nameMatches(name: string | undefined, needle: string): boolean {
  if (!needle) return true
  return String(name || '').toLowerCase().includes(needle)
}

/**
 * 按 query 过滤 folders/leaves；保留命中叶子的祖先目录，以及自身名称命中的目录及其下仍命中的子树。
 * 返回需展开的 folder-* keys（含 root 由调用方决定）。
 * 泛型保留调用方 Folder/Leaf 完整字段（node_type / job_type 等）。
 */
export function filterWorkspaceTree<
  T extends string | number,
  F extends FilterFolder<T>,
  L extends FilterLeaf<T>,
>(opts: {
  folders: F[]
  leaves: L[]
  query: string
}): {
  folders: F[]
  leaves: L[]
  expandFolderKeys: string[]
} {
  const q = String(opts.query || '').trim().toLowerCase()
  if (!q) {
    return {
      folders: opts.folders,
      leaves: opts.leaves,
      expandFolderKeys: [],
    }
  }

  const folders = opts.folders
  const folderById = new Map(folders.map(f => [String(f.id), f]))

  const ancestorsOf = (folderId: T | null | undefined): F[] => {
    const out: F[] = []
    let cur = folderId != null ? folderById.get(String(folderId)) : undefined
    const seen = new Set<string>()
    while (cur) {
      const k = String(cur.id)
      if (seen.has(k)) break
      seen.add(k)
      out.push(cur)
      cur = cur.parent_id != null ? folderById.get(String(cur.parent_id)) : undefined
    }
    return out
  }

  const keepFolderIds = new Set<string>()
  const keepLeafIds = new Set<string>()

  for (const leaf of opts.leaves) {
    if (!nameMatches(leaf.name, q)) continue
    keepLeafIds.add(String(leaf.id))
    for (const a of ancestorsOf(leaf.folder_id ?? null)) {
      keepFolderIds.add(String(a.id))
    }
  }

  for (const folder of folders) {
    if (!nameMatches(folder.name, q)) continue
    keepFolderIds.add(String(folder.id))
    for (const a of ancestorsOf(folder.parent_id)) {
      keepFolderIds.add(String(a.id))
    }
    // 目录名命中时保留其整棵子树（便于浏览该目录下内容）
    const stack = [folder.id]
    const seen = new Set<string>([String(folder.id)])
    while (stack.length) {
      const id = stack.pop()!
      for (const child of folders) {
        if (!sameId(child.parent_id, id)) continue
        const ck = String(child.id)
        if (seen.has(ck)) continue
        seen.add(ck)
        keepFolderIds.add(ck)
        stack.push(child.id)
      }
      for (const leaf of opts.leaves) {
        if (sameId(leaf.folder_id, id)) keepLeafIds.add(String(leaf.id))
      }
    }
  }

  const filteredFolders = folders.filter(f => keepFolderIds.has(String(f.id)))
  const filteredLeaves = opts.leaves.filter(l => keepLeafIds.has(String(l.id)))

  const expandFolderKeys = [...keepFolderIds].map(id => `folder-${id}`)

  return {
    folders: filteredFolders,
    leaves: filteredLeaves,
    expandFolderKeys,
  }
}

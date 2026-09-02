/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { PROBE_DEFAULT_ROW_LIMIT } from './sqlResultRowLimit'

export const probeStorageKey = (wsId: number) => `gido.probe.tree.v1.w${wsId}`

export type ProbeFolder = {
  id: string
  name: string
  parentId: string | null
  /** 0=字典序；>0=用户拖拽序（同 parentId 内） */
  sort_order?: number
}

/** sourceKeys：产生 order 时的结果列序（跟 SQL）；列签名变化后展示改跟新 SQL */
export type ProbeResultColMeta = {
  order: string[]
  widths: Record<string, number>
  sourceKeys?: string[]
}

export type ProbeScript = {
  id: string
  name: string
  folderId: string | null
  sql: string
  datasource_id?: number
  limit: number
  resultColMeta?: ProbeResultColMeta
  /** 0=字典序；>0=用户拖拽序（同 folderId 内） */
  sort_order?: number
}

export type ProbeWorkspaceState = {
  folders: ProbeFolder[]
  scripts: ProbeScript[]
  activeScriptId: string | null
}

export function newProbeId(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`
}

export function defaultProbeState(): ProbeWorkspaceState {
  const id = newProbeId('s')
  return {
    folders: [],
    scripts: [{ id, name: '示例查询', folderId: null, sql: 'SELECT 1 AS probe', limit: PROBE_DEFAULT_ROW_LIMIT }],
    activeScriptId: id,
  }
}

export function loadProbeState(wsId: number | undefined): ProbeWorkspaceState | null {
  if (!wsId) return null
  try {
    const raw = localStorage.getItem(probeStorageKey(wsId))
    if (!raw) return null
    const o = JSON.parse(raw) as ProbeWorkspaceState
    if (!o || !Array.isArray(o.scripts)) return null
    if (!o.scripts.length) return null
    return {
      folders: Array.isArray(o.folders)
        ? o.folders.map(f => ({ ...f, sort_order: f.sort_order ?? 0 }))
        : [],
      scripts: o.scripts.map(s => ({ ...s, sort_order: s.sort_order ?? 0 })),
      activeScriptId:
        o.activeScriptId && o.scripts.some(s => s.id === o.activeScriptId) ? o.activeScriptId : o.scripts[0].id,
    }
  } catch {
    return null
  }
}

export function saveProbeState(wsId: number, state: ProbeWorkspaceState) {
  try {
    localStorage.setItem(probeStorageKey(wsId), JSON.stringify(state))
  } catch {
    /* ignore */
  }
}

/**
 * 将本地私有树中不存在于远端共享树的脚本/文件夹合并进去。
 * 用于首次切换到共享树时，避免丢失各用户已有的本地脚本。
 *
 * 合并规则：
 *   - 以 id 去重，远端已有的脚本/文件夹不覆盖；
 *   - 本地独有的脚本放到根目录（folderId=null），sort_order 排在末尾；
 *   - 本地独有的文件夹同样追加（但其子脚本的 folderId 引用仍有效）；
 *   - activeScriptId 保持远端的不变；
 *   - 若本地无新增，直接返回 remote 引用（无副作用）。
 */
export function mergeLocalIntoRemote(
  remote: ProbeWorkspaceState,
  local: ProbeWorkspaceState,
): { merged: ProbeWorkspaceState; changed: boolean } {
  const remoteScriptIds = new Set(remote.scripts.map(s => s.id))
  const remoteFolderIds = new Set(remote.folders.map(f => f.id))

  const newFolders = local.folders.filter(f => !remoteFolderIds.has(f.id))
  const newScripts = local.scripts.filter(s => !remoteScriptIds.has(s.id))

  if (!newFolders.length && !newScripts.length) {
    return { merged: remote, changed: false }
  }

  const maxScriptOrder = remote.scripts.reduce((m, s) => Math.max(m, s.sort_order ?? 0), 0)
  const maxFolderOrder = remote.folders.reduce((m, f) => Math.max(m, f.sort_order ?? 0), 0)

  const mergedFolders: ProbeFolder[] = [
    ...remote.folders,
    ...newFolders.map((f, i) => ({ ...f, sort_order: maxFolderOrder + (i + 1) * 10 })),
  ]

  const mergedScripts: ProbeScript[] = [
    ...remote.scripts,
    ...newScripts.map((s, i) => ({
      ...s,
      // 只有当本地脚本的 folderId 在合并后的树里不存在时才归到根
      folderId:
        s.folderId && mergedFolders.some(f => f.id === s.folderId)
          ? s.folderId
          : null,
      sort_order: maxScriptOrder + (i + 1) * 10,
    })),
  ]

  return {
    merged: {
      folders: mergedFolders,
      scripts: mergedScripts,
      activeScriptId: remote.activeScriptId,
    },
    changed: true,
  }
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export const probeStorageKey = (wsId: number) => `gido.probe.tree.v1.w${wsId}`

export type ProbeFolder = { id: string; name: string; parentId: string | null }

export type ProbeResultColMeta = { order: string[]; widths: Record<string, number> }

export type ProbeScript = {
  id: string
  name: string
  folderId: string | null
  sql: string
  datasource_id?: number
  limit: number
  resultColMeta?: ProbeResultColMeta
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
    scripts: [{ id, name: '示例查询', folderId: null, sql: 'SELECT 1 AS probe', limit: 500 }],
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
      folders: Array.isArray(o.folders) ? o.folders : [],
      scripts: o.scripts,
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

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * 数据开发本地草稿兜底（断网/接口失败时防丢）；权威仍以服务端静默保存为准。
 */

const PREFIX = 'gido.studio.draft.v1'

export type StudioLocalDraft = {
  script: string
  updatedAt: number
  serverUpdatedAt?: string | null
}

function key(workspaceId: number, nodeId: number) {
  return `${PREFIX}.${workspaceId}.${nodeId}`
}

export function readStudioLocalDraft(workspaceId: number, nodeId: number): StudioLocalDraft | null {
  try {
    const raw = localStorage.getItem(key(workspaceId, nodeId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as StudioLocalDraft
    if (!parsed || typeof parsed.script !== 'string') return null
    return parsed
  } catch {
    return null
  }
}

export function writeStudioLocalDraft(
  workspaceId: number,
  nodeId: number,
  script: string,
  serverUpdatedAt?: string | null,
) {
  try {
    const payload: StudioLocalDraft = {
      script,
      updatedAt: Date.now(),
      serverUpdatedAt: serverUpdatedAt ?? null,
    }
    localStorage.setItem(key(workspaceId, nodeId), JSON.stringify(payload))
  } catch {
    /* quota / private mode */
  }
}

export function clearStudioLocalDraft(workspaceId: number, nodeId: number) {
  try {
    localStorage.removeItem(key(workspaceId, nodeId))
  } catch {
    /* ignore */
  }
}

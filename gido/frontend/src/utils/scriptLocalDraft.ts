/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * 脚本编辑本地草稿兜底（Studio / Stream / Probe / NodeConfig 共用）。
 */

const PREFIX = 'gido.scriptDraft.v1'

export type ScriptLocalDraft = {
  script: string
  updatedAt: number
}

export function scriptDraftStorageKey(scope: string, entityId: string | number) {
  return `${PREFIX}.${scope}.${entityId}`
}

export function readScriptLocalDraft(storageKey: string | null | undefined): ScriptLocalDraft | null {
  if (!storageKey) return null
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ScriptLocalDraft
    if (!parsed || typeof parsed.script !== 'string') return null
    return parsed
  } catch {
    return null
  }
}

export function writeScriptLocalDraft(storageKey: string | null | undefined, script: string) {
  if (!storageKey) return
  try {
    const payload: ScriptLocalDraft = { script, updatedAt: Date.now() }
    localStorage.setItem(storageKey, JSON.stringify(payload))
  } catch {
    /* quota / private mode */
  }
}

export function clearScriptLocalDraft(storageKey: string | null | undefined) {
  if (!storageKey) return
  try {
    localStorage.removeItem(storageKey)
  } catch {
    /* ignore */
  }
}

/** 打开实体时：若本地草稿与服务端不一致则返回本地脚本，否则 null */
export function restoreScriptLocalDraft(
  storageKey: string | null | undefined,
  serverScript: string,
): string | null {
  const draft = readScriptLocalDraft(storageKey)
  if (!draft) return null
  if (draft.script === (serverScript ?? '')) return null
  return draft.script
}

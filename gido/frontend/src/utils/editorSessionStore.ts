/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * IDEA 式编辑器会话：只持久化 tab id / activeId，不存脚本正文。
 * 首屏仍走 slim list + 按需 getNode/getJob hydrate。
 */
export type EditorSessionScope = 'studio' | 'stream'

export type EditorSession = {
  tabIds: number[]
  activeId: number | null
  updatedAt: number
}

export const EDITOR_SESSION_MAX_TABS = 12

function storageKey(scope: EditorSessionScope, workspaceId: number): string {
  return `gido.editorSession.v1.${scope}.${workspaceId}`
}

export function readEditorSession(
  scope: EditorSessionScope,
  workspaceId: number | undefined | null,
): EditorSession | null {
  if (workspaceId == null) return null
  try {
    const raw = localStorage.getItem(storageKey(scope, workspaceId))
    if (!raw) return null
    const o = JSON.parse(raw) as Partial<EditorSession>
    if (!o || !Array.isArray(o.tabIds)) return null
    const tabIds = o.tabIds
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id) && id > 0)
    const activeId =
      o.activeId != null && Number.isFinite(Number(o.activeId)) ? Number(o.activeId) : null
    return {
      tabIds,
      activeId,
      updatedAt: typeof o.updatedAt === 'number' ? o.updatedAt : Date.now(),
    }
  } catch {
    return null
  }
}

export function writeEditorSession(
  scope: EditorSessionScope,
  workspaceId: number,
  session: { tabIds: number[]; activeId: number | null },
): void {
  try {
    const normalized = normalizeEditorSession(session.tabIds, session.activeId)
    localStorage.setItem(
      storageKey(scope, workspaceId),
      JSON.stringify({ ...normalized, updatedAt: Date.now() } satisfies EditorSession),
    )
  } catch {
    /* quota / private mode */
  }
}

export function clearEditorSession(
  scope: EditorSessionScope,
  workspaceId: number,
): void {
  try {
    localStorage.removeItem(storageKey(scope, workspaceId))
  } catch {
    /* ignore */
  }
}

/** 过滤已删除 id，保证 active 在列表内，并裁剪至上限（优先保留 active） */
export function normalizeEditorSession(
  tabIds: number[],
  activeId: number | null,
  opts?: { maxTabs?: number; existingIds?: Set<number> | number[] },
): { tabIds: number[]; activeId: number | null } {
  const max = opts?.maxTabs ?? EDITOR_SESSION_MAX_TABS
  const exist = opts?.existingIds
    ? opts.existingIds instanceof Set
      ? opts.existingIds
      : new Set(opts.existingIds)
    : null

  const seen = new Set<number>()
  let ids: number[] = []
  for (const raw of tabIds) {
    const id = Number(raw)
    if (!Number.isFinite(id) || id <= 0 || seen.has(id)) continue
    if (exist && !exist.has(id)) continue
    seen.add(id)
    ids.push(id)
  }

  let active =
    activeId != null && Number.isFinite(activeId) && seen.has(Number(activeId))
      ? Number(activeId)
      : ids[0] ?? null

  if (ids.length > max) {
    const keep = new Set<number>()
    if (active != null) keep.add(active)
    for (const id of ids) {
      if (keep.size >= max) break
      keep.add(id)
    }
    ids = ids.filter((id) => keep.has(id))
    if (active != null && !keep.has(active)) active = ids[0] ?? null
  }

  return { tabIds: ids, activeId: active }
}

/** 兼容旧版「仅记一个 last node」key */
export function readLegacyStudioLastNodeId(workspaceId: number): number | null {
  try {
    const raw = localStorage.getItem('gido.studio.lastNodeByWorkspace')
    if (!raw) return null
    const map = JSON.parse(raw) as Record<string, number>
    const id = map[String(workspaceId)]
    return typeof id === 'number' && Number.isFinite(id) ? id : null
  } catch {
    return null
  }
}

const writeTimers = new Map<string, ReturnType<typeof setTimeout>>()

/** 防抖写入，避免切 Tab 时刷爆 localStorage */
export function scheduleWriteEditorSession(
  scope: EditorSessionScope,
  workspaceId: number,
  session: { tabIds: number[]; activeId: number | null },
  debounceMs = 200,
): void {
  const key = storageKey(scope, workspaceId)
  const prev = writeTimers.get(key)
  if (prev) clearTimeout(prev)
  writeTimers.set(
    key,
    setTimeout(() => {
      writeTimers.delete(key)
      writeEditorSession(scope, workspaceId, session)
    }, debounceMs),
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  EDITOR_SESSION_MAX_TABS,
  clearEditorSession,
  normalizeEditorSession,
  readEditorSession,
  readLegacyStudioLastNodeId,
  writeEditorSession,
} from './editorSessionStore'

function installMemoryStorage() {
  const map = new Map<string, string>()
  const storage = {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => { map.set(k, String(v)) },
    removeItem: (k: string) => { map.delete(k) },
    clear: () => { map.clear() },
    get length() { return map.size },
    key: (i: number) => [...map.keys()][i] ?? null,
  }
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
  return storage
}

describe('normalizeEditorSession', () => {
  it('去重、过滤非法 id，并保证 active 在列表内', () => {
    const r = normalizeEditorSession([1, 2, 2, 0, -1, 3], 99)
    expect(r.tabIds).toEqual([1, 2, 3])
    expect(r.activeId).toBe(1)
  })

  it('按 existingIds 过滤已删节点', () => {
    const r = normalizeEditorSession([1, 2, 3], 2, { existingIds: [1, 3] })
    expect(r.tabIds).toEqual([1, 3])
    expect(r.activeId).toBe(1)
  })

  it('超出上限时优先保留 active', () => {
    const ids = Array.from({ length: EDITOR_SESSION_MAX_TABS + 5 }, (_, i) => i + 1)
    const active = EDITOR_SESSION_MAX_TABS + 3
    const r = normalizeEditorSession(ids, active)
    expect(r.tabIds).toHaveLength(EDITOR_SESSION_MAX_TABS)
    expect(r.tabIds).toContain(active)
    expect(r.activeId).toBe(active)
  })
})

describe('editorSessionStore localStorage', () => {
  beforeEach(() => {
    installMemoryStorage()
  })
  afterEach(() => {
    localStorage.clear()
  })

  it('读写 studio / stream 会话', () => {
    writeEditorSession('studio', 7, { tabIds: [10, 20], activeId: 20 })
    expect(readEditorSession('studio', 7)).toMatchObject({
      tabIds: [10, 20],
      activeId: 20,
    })
    expect(readEditorSession('stream', 7)).toBeNull()
    writeEditorSession('stream', 7, { tabIds: [5], activeId: 5 })
    expect(readEditorSession('stream', 7)?.activeId).toBe(5)
    clearEditorSession('studio', 7)
    expect(readEditorSession('studio', 7)).toBeNull()
  })

  it('兼容旧版 lastNodeByWorkspace', () => {
    localStorage.setItem(
      'gido.studio.lastNodeByWorkspace',
      JSON.stringify({ '9': 42 }),
    )
    expect(readLegacyStudioLastNodeId(9)).toBe(42)
    expect(readLegacyStudioLastNodeId(1)).toBeNull()
  })
})

describe('scheduleWriteEditorSession', () => {
  beforeEach(() => {
    installMemoryStorage()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
    localStorage.clear()
  })

  it('防抖写入', async () => {
    const { scheduleWriteEditorSession } = await import('./editorSessionStore')
    scheduleWriteEditorSession('studio', 1, { tabIds: [1], activeId: 1 }, 200)
    scheduleWriteEditorSession('studio', 1, { tabIds: [1, 2], activeId: 2 }, 200)
    expect(readEditorSession('studio', 1)).toBeNull()
    vi.advanceTimersByTime(200)
    expect(readEditorSession('studio', 1)).toMatchObject({
      tabIds: [1, 2],
      activeId: 2,
    })
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 会话恢复协议流程测试（可在 CI 跑，不依赖真实后端）。
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  canPersistEditorSession,
  clearEditorSession,
  isEditorTabContentPending,
  normalizeEditorSession,
  readEditorSession,
  writeEditorSession,
} from './editorSessionStore'
import { planStudioSessionTabOrder, resolveStudioTabChrome } from './studioTabChrome'

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
}

describe('studio session restore flow', () => {
  beforeEach(() => {
    installMemoryStorage()
  })
  afterEach(() => {
    localStorage.clear()
  })

  it('seeds all tab shells in one plan then hydrates only active', () => {
    const stored = { tabIds: [10, 20, 30], activeId: 20 }
    const existing = new Set([10, 20, 30, 40])
    const normalized = normalizeEditorSession(stored.tabIds, stored.activeId, { existingIds: existing })
    expect(normalized.tabIds).toEqual([10, 20, 30])

    const stubs = normalized.tabIds.map(id => ({
      id,
      name: `n${id}`,
      node_type: 'SQL',
      script_content: null as string | null,
    }))
    for (const t of stubs) {
      expect(resolveStudioTabChrome({ script_content: t.script_content })).toBe('pending')
    }

    const active = stubs.find(t => t.id === normalized.activeId)!
    active.script_content = 'SELECT active'
    expect(resolveStudioTabChrome({ script_content: active.script_content })).toBe('ready')
    expect(stubs.filter(t => isEditorTabContentPending(t)).map(t => t.id)).toEqual([10, 30])
  })

  it('failed hydrate stays error until force retry succeeds', () => {
    expect(resolveStudioTabChrome({ script_content: null, error: '502' })).toBe('error')
    expect(resolveStudioTabChrome({ script_content: null, loading: true })).toBe('loading')
    expect(resolveStudioTabChrome({ script_content: 'SELECT ok' })).toBe('ready')
  })

  it('closing removes id from persisted session', () => {
    writeEditorSession('studio', 1, { tabIds: [1, 2, 3], activeId: 2 })
    expect(canPersistEditorSession({ hydrated: true })).toBe(true)
    const afterClose = normalizeEditorSession([1, 3], 3)
    writeEditorSession('studio', 1, afterClose)
    expect(readEditorSession('studio', 1)?.tabIds).toEqual([1, 3])
    clearEditorSession('studio', 1)
  })

  it('deep-link prefer prepends without dropping other session tabs', () => {
    const planned = planStudioSessionTabOrder([2, 3], 99)
    expect(planned).toEqual({ tabIds: [99, 2, 3], activeId: 99 })
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { beforeEach, describe, expect, it } from 'vitest'
import { pushRecent, readShortcuts, togglePin } from './dataMapPins'
import { queuePendingProbeScript, takePendingProbeScript } from './probePendingScript'

function memoryStorage() {
  const data = new Map<string, string>()
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value) },
    removeItem: (key: string) => { data.delete(key) },
    clear: () => { data.clear() },
  }
}

describe('data map shortcuts and probe handoff', () => {
  beforeEach(() => {
    const storage = memoryStorage()
    Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
    Object.defineProperty(globalThis, 'sessionStorage', { value: memoryStorage(), configurable: true })
  })

  it('toggles pins and keeps recents unique', () => {
    const pin = { datasourceId: 1, catalog: 'ads', tableName: 'orders' }
    expect(togglePin(1, pin)).toEqual([pin])
    expect(togglePin(1, pin)).toEqual([])
    togglePin(1, pin)
    pushRecent(1, pin)
    pushRecent(1, { ...pin, comment: 'again' })
    expect(readShortcuts(1, 'recent')).toHaveLength(1)
    expect(readShortcuts(1, 'recent')[0].comment).toBe('again')
  })

  it('hands a probe script over once', () => {
    queuePendingProbeScript({ name: 'orders', sql: 'SELECT 1', datasourceId: 3 })
    expect(takePendingProbeScript()).toEqual({ name: 'orders', sql: 'SELECT 1', datasourceId: 3 })
    expect(takePendingProbeScript()).toBeNull()
  })
})

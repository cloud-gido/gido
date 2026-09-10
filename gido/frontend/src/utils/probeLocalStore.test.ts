/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 单元：探查本地树水合；回归：有缓存时进页不应挡全屏「加载探查目录」。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import {
  initialProbeWorkspaceState,
  loadProbeState,
  probeStorageKey,
  probeTreeReadyFromCache,
  saveProbeState,
  uniqueProbeCopyName,
  type ProbeWorkspaceState,
} from './probeLocalStore'

const WS = 42

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

function sampleState(name = '我的查询'): ProbeWorkspaceState {
  return {
    folders: [],
    scripts: [{ id: 's-1', name, folderId: null, sql: 'SELECT 1', limit: 10000 }],
    activeScriptId: 's-1',
  }
}

describe('probeLocalStore hydrate', () => {
  beforeEach(() => {
    installMemoryStorage()
    localStorage.removeItem(probeStorageKey(WS))
  })

  it('probeTreeReadyFromCache is true only when local tree exists', () => {
    expect(probeTreeReadyFromCache(undefined)).toBe(false)
    expect(probeTreeReadyFromCache(WS)).toBe(false)
    saveProbeState(WS, sampleState())
    expect(probeTreeReadyFromCache(WS)).toBe(true)
  })

  it('initialProbeWorkspaceState prefers local cache over default 示例查询', () => {
    saveProbeState(WS, sampleState('缓存查询'))
    const init = initialProbeWorkspaceState(WS)
    expect(init.scripts[0].name).toBe('缓存查询')
    expect(init.activeScriptId).toBe('s-1')
  })

  it('initialProbeWorkspaceState falls back to default when cold', () => {
    const init = initialProbeWorkspaceState(WS)
    expect(init.scripts.length).toBe(1)
    expect(init.scripts[0].name).toBe('示例查询')
  })

  it('round-trips save/load', () => {
    const s = sampleState()
    saveProbeState(WS, s)
    expect(loadProbeState(WS)?.scripts[0].sql).toBe('SELECT 1')
  })

  it('uniqueProbeCopyName appends -copy / -copy-n', () => {
    expect(uniqueProbeCopyName(['a'], 'demo')).toBe('demo-copy')
    expect(uniqueProbeCopyName(['demo-copy'], 'demo')).toBe('demo-copy-2')
    expect(uniqueProbeCopyName(['demo-copy', 'demo-copy-2'], 'demo')).toBe('demo-copy-3')
  })
})

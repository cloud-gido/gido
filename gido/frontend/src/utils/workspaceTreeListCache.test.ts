/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, describe, expect, it } from 'vitest'
import {
  clearTreeListCacheForTests,
  loadTreeListCache,
  saveTreeListCache,
  treeListReadyFromCache,
} from './workspaceTreeListCache'

const WS = 42

afterEach(() => {
  clearTreeListCacheForTests()
})

describe('workspaceTreeListCache', () => {
  it('round-trips studio snapshot in memory', () => {
    expect(treeListReadyFromCache('studio', WS)).toBe(false)
    saveTreeListCache('studio', WS, { folders: [{ id: 1 }], leaves: [{ id: 10 }] })
    expect(treeListReadyFromCache('studio', WS)).toBe(true)
    expect(loadTreeListCache('studio', WS)?.leaves).toEqual([{ id: 10 }])
  })

  it('stream kind uses separate keys', () => {
    saveTreeListCache('studio', WS, { folders: [], leaves: [{ id: 'a' }] })
    saveTreeListCache('stream', WS, { folders: [], leaves: [{ id: 'b' }] })
    expect(loadTreeListCache('studio', WS)?.leaves).toEqual([{ id: 'a' }])
    expect(loadTreeListCache('stream', WS)?.leaves).toEqual([{ id: 'b' }])
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('Stream Studio tree hydrate adoption', () => {
  it('uses workspaceTreeListCache and avoids spinner when memory cache exists', () => {
    const page = read('pages/StreamStudio.tsx')
    expect(page).toContain('workspaceTreeListCache')
    expect(page).toContain('treeListReadyFromCache')
    expect(page).toContain('treeReady')
    expect(page).toContain('load(!hadCache)')
  })
})

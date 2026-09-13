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

describe('Studio tree hydrate adoption', () => {
  it('uses workspaceTreeListCache and sidebar skeleton while list syncs', () => {
    const studio = read('pages/Studio.tsx')
    expect(studio).toContain('workspaceTreeListCache')
    expect(studio).toContain('treeListReadyFromCache')
    expect(studio).toContain('saveTreeListCache')
    expect(studio).toContain('treeReady')
    expect(studio).toContain('Skeleton')
  })
})

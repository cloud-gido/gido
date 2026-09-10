/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：目录树搜索与复制必须在共享组件 + 三端接线，禁止只改一端。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('workspace folder tree search + copy adoption', () => {
  it('WorkspaceFolderTree owns name filter UI and filterWorkspaceTree', () => {
    const tree = read('components/WorkspaceFolderTree.tsx')
    expect(tree).toContain('filterWorkspaceTree')
    expect(tree).toContain('SearchOutlined')
    expect(tree).toContain('查找…')
    expect(tree).toContain('无匹配项')
    expect(tree).toContain("label: '复制'")
  })

  it('Studio / Probe / Stream all wire onCopyLeaf', () => {
    expect(read('pages/Studio.tsx')).toContain('onCopyLeaf')
    expect(read('pages/Probe.tsx')).toContain('onCopyLeaf')
    expect(read('pages/StreamStudio.tsx')).toContain('onCopyLeaf')
  })

  it('Studio uses studioApi.copyNode', () => {
    const api = read('api/index.ts')
    expect(api).toContain('copyNode:')
    expect(api).toContain('/studio/nodes/${id}/copy')
    expect(read('pages/Studio.tsx')).toContain('studioApi.copyNode')
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：空间优先 — 统一工作台壳跨批/流/服保持挂载，切子产品不重拉空间列表。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { productFromPath } from './productFromPath'

const root = resolve(__dirname, '../..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('workspace chrome across products', () => {
  it('persists workspaces in app store and uses stable select labels', () => {
    const store = read('store/index.ts')
    expect(store).toContain('workspaces:')
    expect(store).toContain('setWorkspaces:')

    const shell = read('components/shell/useWorkspaceShell.ts')
    expect(shell).toContain('workspaces')
    expect(shell).toContain('setWorkspaces')
    expect(shell).toContain('workspacesFetchStarted')
    expect(shell).not.toMatch(/useState<any\[\]>\(\[\]\)/)

    const bar = read('components/shell/WorkspaceHeaderBar.tsx')
    expect(bar).toContain('buildWorkspaceSelectOptions')
    expect(bar).toContain('workspaceSelectDisplayLabel')
    expect(bar).toContain('labelRender')
  })

  it('App mounts one ProductWorkspaceShell under /gido for all products', () => {
    const app = read('App.tsx')
    expect(app).toContain('ProductWorkspaceShell')
    expect(app).toContain('path="/gido"')
    expect(app).not.toMatch(/MainLayout|StreamLayout|ServiceLayout/)

    const shell = read('components/shell/ProductWorkspaceShell.tsx')
    expect(shell).toContain('useWorkspaceShell')
    expect(shell).toContain('WorkspaceHeaderBar')
    expect(shell).toContain('BatchProductSider')
    expect(shell).toContain('StreamProductSider')
    expect(shell).toContain('ServiceProductSider')
  })

  it('productFromPath maps batch/stream/service without touching workspace', () => {
    expect(productFromPath('/gido/batch/studio')).toBe('batch')
    expect(productFromPath('/gido/stream/monitor')).toBe('stream')
    expect(productFromPath('/gido/service/apis')).toBe('service')
    expect(productFromPath('/gido/batch')).toBe('batch')
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 集成契约：批开发 / 实时作业开发 / 数据探查必须复用同一工作台壳，
 * 禁止回退到各自复制的 bleed ResizableSidebar 布局。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function readPage(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

const PAGES = [
  { name: 'Studio', path: 'pages/Studio.tsx' },
  { name: 'StreamStudio', path: 'pages/StreamStudio.tsx' },
  { name: 'Probe', path: 'pages/Probe.tsx' },
] as const

describe('studio workbench shell adoption', () => {
  it.each(PAGES)('$name imports and mounts StudioWorkbenchShell', ({ path }) => {
    const src = readPage(path)
    expect(src).toContain("from '../components/StudioWorkbenchShell'")
    expect(src).toContain('<StudioWorkbenchShell')
    expect(src).toContain('</StudioWorkbenchShell>')
  })

  it.each(PAGES)('$name does not reintroduce page-local bleed ResizableSidebar', ({ path }) => {
    const src = readPage(path)
    expect(src).not.toContain("from '../components/ResizableSidebar'")
    expect(src).not.toContain("height: 'calc(100vh - 112px)', margin: -24")
  })

  it('shared shell owns the bleed style constant', () => {
    const src = readPage('components/StudioWorkbenchShell.tsx')
    expect(src).toContain('STUDIO_WORKBENCH_BLEED_STYLE')
    expect(src).toContain("height: 'calc(100vh - 112px)'")
    expect(src).toContain('margin: -24')
    expect(src).toContain("from './ResizableSidebar'")
  })
})

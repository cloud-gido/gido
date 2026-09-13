/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 约束：Monaco 必须走仓库内 monaco-editor，禁止运行时 jsDelivr/CDN。
 */
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname)

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

const EDITOR_SURFACES = [
  'pages/Studio.tsx',
  'pages/Probe.tsx',
  'pages/StreamStudio.tsx',
  'components/NodeConfigModal.tsx',
  'components/DwMonacoEditor.tsx',
] as const

describe('monaco local bundle', () => {
  it('setupMonacoLocal pins @monaco-editor/react to npm monaco-editor', () => {
    const src = read('monacoSetup.ts')
    expect(src).toMatch(/loader\.config\(\{\s*monaco\s*\}\)/)
    expect(src).toMatch(/from 'monaco-editor\/esm\/vs\/editor\/editor.main'/)
    expect(src).not.toMatch(/jsdelivr|unpkg|cdnjs/)
  })

  it.each(EDITOR_SURFACES)('%s imports monacoSetup before the editor', (rel) => {
    const src = read(rel)
    expect(src).toMatch(/monacoSetup/)
    const setupAt = src.indexOf('monacoSetup')
    const editorAt = src.search(/from ['"]@monaco-editor\/react['"]/)
    expect(setupAt).toBeGreaterThanOrEqual(0)
    expect(editorAt).toBeGreaterThan(setupAt)
  })
})

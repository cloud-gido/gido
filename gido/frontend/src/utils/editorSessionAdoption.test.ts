/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 契约：脚本快捷键与会话恢复须经共享层，禁止四端各自实现。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('editor session + script keybindings adoption', () => {
  it('Studio / Stream / Probe / NodeConfigModal 均绑定 bindMonacoScriptKeybindings', () => {
    for (const rel of [
      'pages/Studio.tsx',
      'pages/StreamStudio.tsx',
      'pages/Probe.tsx',
      'components/NodeConfigModal.tsx',
    ]) {
      const src = read(rel)
      expect(src).toContain('monacoScriptKeybindings')
      expect(src).toContain('bindMonacoScriptKeybindings')
      expect(src).toContain('bindMonacoFindKeybindings')
    }
  })

  it('Studio / Stream 使用 editorSessionStore；Probe / NodeConfigModal 不叠 Tab session', () => {
    const studio = read('pages/Studio.tsx')
    const stream = read('pages/StreamStudio.tsx')
    const probe = read('pages/Probe.tsx')
    const modal = read('components/NodeConfigModal.tsx')
    expect(studio).toContain('readEditorSession')
    expect(studio).toContain("scheduleWriteEditorSession('studio'")
    expect(studio).toContain('canPersistEditorSession')
    expect(studio).toContain('studioSessionHydratedRef')
    expect(studio).toContain('cancelScheduledEditorSessionWrite')
    expect(stream).toContain('readEditorSession')
    expect(stream).toContain("scheduleWriteEditorSession('stream'")
    expect(stream).toContain('canPersistEditorSession')
    expect(stream).toContain('streamSessionHydratedRef')
    expect(probe).not.toContain('editorSessionStore')
    expect(modal).not.toContain('editorSessionStore')
  })
})

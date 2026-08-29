/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it, vi } from 'vitest'
import {
  bindMonacoScriptKeybindings,
  scriptForRun,
} from './monacoScriptKeybindings'

function mockEditor(opts: {
  selection?: string
  value?: string
}) {
  const commands: Array<{ key: number; fn: () => void }> = []
  return {
    ed: {
      getSelection: () => (opts.selection ? { isEmpty: () => false } : { isEmpty: () => true }),
      getModel: () => ({
        getValueInRange: () => opts.selection ?? '',
      }),
      getValue: () => opts.value ?? '',
      addCommand: (key: number, fn: () => void) => {
        commands.push({ key, fn })
      },
      trigger: vi.fn(),
    } as any,
    commands,
  }
}

const monaco = {
  KeyMod: { CtrlCmd: 2048 },
  KeyCode: { Enter: 3, Slash: 85 },
}

describe('scriptForRun', () => {
  it('有选中时用选中片段', () => {
    const { ed } = mockEditor({ selection: '  SELECT 1  ', value: 'SELECT 1;\nSELECT 2' })
    // selectionText 读 model.getValueInRange；mock 需对齐 monacoCustomFind
    const r = scriptForRun(ed)
    expect(r.fromSelection).toBe(true)
    expect(r.script).toBe('SELECT 1')
  })

  it('无选中时用全文', () => {
    const { ed } = mockEditor({ value: 'SELECT 2' })
    const r = scriptForRun(ed)
    expect(r.fromSelection).toBe(false)
    expect(r.script).toBe('SELECT 2')
  })
})

describe('bindMonacoScriptKeybindings', () => {
  it('Cmd+/ 触发 commentLine；Cmd+Enter 传选中或全文', () => {
    const onRun = vi.fn()
    const { ed, commands } = mockEditor({ selection: 'SELECT 1', value: 'full' })
    bindMonacoScriptKeybindings(ed, monaco, { onRun })
    expect(commands).toHaveLength(2)

    const slash = commands.find(c => c.key === (2048 | 85))
    const enter = commands.find(c => c.key === (2048 | 3))
    expect(slash).toBeTruthy()
    expect(enter).toBeTruthy()

    slash!.fn()
    expect(ed.trigger).toHaveBeenCalledWith('keyboard', 'editor.action.commentLine', null)

    enter!.fn()
    expect(onRun).toHaveBeenCalledWith('SELECT 1', { fromSelection: true })
  })

  it('enableRun 为 false 时不调用 onRun', () => {
    const onRun = vi.fn()
    const { ed, commands } = mockEditor({ value: 'SELECT 1' })
    bindMonacoScriptKeybindings(ed, monaco, {
      onRun,
      enableRun: () => false,
    })
    const enter = commands.find(c => c.key === (2048 | 3))
    enter!.fn()
    expect(onRun).not.toHaveBeenCalled()
  })
})

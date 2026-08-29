/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 共享 Monaco 脚本快捷键：Cmd/Ctrl+Enter 试跑、Cmd/Ctrl+/ 注释。
 * 选中可执行语句时行号旁 ▶（常见 SQL IDE 选区运行）。
 * Studio / Probe / Stream / NodeConfigModal 须同一套绑定。
 */
import type { editor } from 'monaco-editor'
import { selectionText } from './monacoCustomFind'
import {
  bindMonacoSelectionRunGlyph,
  type MonacoGlyphApi,
} from './monacoSelectionRunGlyph'

export type MonacoLike = MonacoGlyphApi & {
  KeyMod: { CtrlCmd: number }
  KeyCode: { Enter: number; Slash: number }
}

export type ScriptKeybindingOptions = {
  /** 试跑回调；未提供则不绑定 Cmd+Enter / 选区 ▶ */
  onRun?: (script: string, meta: { fromSelection: boolean }) => void
  /** 动态开关试跑（只读 / 非 SQL 等） */
  enableRun?: boolean | (() => boolean)
}

function runEnabled(opts: ScriptKeybindingOptions): boolean {
  if (!opts.onRun) return false
  const e = opts.enableRun
  if (e === undefined) return true
  return typeof e === 'function' ? e() : e
}

/** 取选中片段，否则全文 */
export function scriptForRun(ed: editor.IStandaloneCodeEditor): {
  script: string
  fromSelection: boolean
} {
  const selected = selectionText(ed).trim()
  if (selected) return { script: selected, fromSelection: true }
  const full = ed.getValue() ?? ''
  return { script: full, fromSelection: false }
}

export function bindMonacoScriptKeybindings(
  ed: editor.IStandaloneCodeEditor,
  monaco: MonacoLike,
  opts: ScriptKeybindingOptions = {},
): void {
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Slash, () => {
    ed.trigger('keyboard', 'editor.action.commentLine', null)
  })

  if (opts.onRun) {
    ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
      if (!runEnabled(opts)) return
      const { script, fromSelection } = scriptForRun(ed)
      if (!script.trim()) return
      opts.onRun!(script, { fromSelection })
    })
    bindMonacoSelectionRunGlyph(ed, monaco, opts)
  }
}

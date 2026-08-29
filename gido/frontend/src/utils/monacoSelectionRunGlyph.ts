/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 选中可执行脚本时在行号旁显示 ▶（对齐 DataWorks / DBeaver），
 * 与 bindMonacoScriptKeybindings 共用 onRun。
 */
import type { editor, IDisposable } from 'monaco-editor'
import { selectionText } from './monacoCustomFind'
import './monacoSelectionRunGlyph.css'

export type SelectionRunGlyphOptions = {
  onRun?: (script: string, meta: { fromSelection: boolean }) => void
  enableRun?: boolean | (() => boolean)
}

export type MonacoGlyphApi = {
  Range: new (
    startLineNumber: number,
    startColumn: number,
    endLineNumber: number,
    endColumn: number,
  ) => unknown
  editor: {
    MouseTargetType: { GUTTER_GLYPH_MARGIN: number }
  }
}

function runEnabled(opts: SelectionRunGlyphOptions): boolean {
  if (!opts.onRun) return false
  const e = opts.enableRun
  if (e === undefined) return true
  return typeof e === 'function' ? e() : e
}

/** 选区是否像可执行语句（避免只选中标识符也出 ▶） */
export function isRunnableSelection(text: string): boolean {
  const raw = (text || '').trim()
  if (raw.length < 4) return false
  const lines = raw.split(/\r?\n/)
  let i = 0
  while (
    i < lines.length
    && (lines[i]!.trim() === '' || /^\s*(--|#|\/\/)/.test(lines[i]!))
  ) {
    i += 1
  }
  const body = lines.slice(i).join('\n').trim()
  if (body.length < 4) return false
  return /^(with|select|insert|update|delete|merge|show|desc|describe|explain|create|drop|alter|call|use|set|replace|truncate|analyze|grant|revoke|begin|declare|exec|execute|print)\b/i.test(
    body,
  ) || /^(def|class|import|from|for|while|if|try|print|gido_job|writelog)\b/i.test(body)
}

/**
 * 监听选区：完整可跑语句时在选区起始行 glyph margin 显示运行箭头。
 */
export function bindMonacoSelectionRunGlyph(
  ed: editor.IStandaloneCodeEditor,
  monaco: MonacoGlyphApi,
  opts: SelectionRunGlyphOptions,
): IDisposable[] {
  if (!opts.onRun) return []

  ed.updateOptions({ glyphMargin: true })
  let decoIds: string[] = []
  let glyphLine: number | null = null

  const clear = () => {
    decoIds = ed.deltaDecorations(decoIds, [])
    glyphLine = null
  }

  const refresh = () => {
    if (!runEnabled(opts)) {
      clear()
      return
    }
    const sel = ed.getSelection()
    if (!sel || sel.isEmpty()) {
      clear()
      return
    }
    const text = selectionText(ed)
    if (!isRunnableSelection(text)) {
      clear()
      return
    }
    const line = Math.min(sel.startLineNumber, sel.endLineNumber)
    glyphLine = line
    decoIds = ed.deltaDecorations(decoIds, [
      {
        range: new monaco.Range(line, 1, line, 1) as editor.IRange,
        options: {
          glyphMarginClassName: 'gido-sql-run-glyph',
          glyphMarginHoverMessage: { value: '运行选中片段（⌘/Ctrl+Enter）' },
        },
      },
    ])
  }

  const d1 = ed.onDidChangeCursorSelection(() => refresh())
  const d2 = ed.onDidChangeModelContent(() => refresh())
  const d3 = ed.onMouseDown((e) => {
    if (e.target.type !== monaco.editor.MouseTargetType.GUTTER_GLYPH_MARGIN) return
    if (glyphLine == null) return
    const line = e.target.position?.lineNumber
    if (line !== glyphLine) return
    if (!runEnabled(opts)) return
    const text = selectionText(ed).trim()
    if (!text) return
    e.event.preventDefault?.()
    e.event.stopPropagation?.()
    opts.onRun!(text, { fromSelection: true })
  })

  refresh()
  return [d1, d2, d3]
}

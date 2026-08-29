/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 选中可执行脚本时在行号旁显示 ▶（对标常见 SQL IDE 的选区运行），
 * 与 bindMonacoScriptKeybindings 共用 onRun。
 *
 * 使用 Monaco overlay DOM（不用 glyphMarginClassName），避免装饰 CSS
 * 因主题/布局差异不可见。
 */
import type { editor, IDisposable, IRange } from 'monaco-editor'
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
  ) => IRange
  editor: {
    MouseTargetType: {
      GUTTER_GLYPH_MARGIN: number
      GUTTER_LINE_DECORATIONS?: number
      GUTTER_LINE_NUMBERS?: number
    }
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
 * 监听选区：完整可跑语句时在选区起始行旁显示运行按钮。
 */
export function bindMonacoSelectionRunGlyph(
  ed: editor.IStandaloneCodeEditor,
  _monaco: MonacoGlyphApi,
  opts: SelectionRunGlyphOptions,
): IDisposable[] {
  if (!opts.onRun) return []

  // 给行装饰留位，按钮叠在行号与正文之间
  ed.updateOptions({ glyphMargin: true, lineDecorationsWidth: 18 })

  const host = ed.getDomNode()
  if (!host) return []

  const btn = document.createElement('button')
  btn.type = 'button'
  btn.className = 'gido-sql-run-btn'
  btn.title = '运行选中片段（⌘/Ctrl+Enter）'
  btn.setAttribute('aria-label', '运行选中片段')
  btn.tabIndex = -1
  btn.hidden = true
  btn.innerHTML =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">'
    + '<path fill="currentColor" d="M4.5 2.5v11l9-5.5-9-5.5z"/>'
    + '</svg>'
  host.appendChild(btn)

  let glyphLine: number | null = null

  const hide = () => {
    glyphLine = null
    btn.hidden = true
  }

  const place = () => {
    if (glyphLine == null) {
      hide()
      return
    }
    const vis = ed.getScrolledVisiblePosition({ lineNumber: glyphLine, column: 1 })
    if (!vis) {
      hide()
      return
    }
    const layout = ed.getLayoutInfo()
    // 行号右侧装饰槽；没有则贴在正文左侧
    const left = layout.decorationsLeft > 0
      ? layout.decorationsLeft + Math.max(0, (layout.decorationsWidth - 14) / 2)
      : Math.max(2, layout.contentLeft - 16)
    btn.style.top = `${vis.top + Math.max(0, (vis.height - 16) / 2)}px`
    btn.style.left = `${left}px`
    btn.hidden = false
  }

  const refresh = () => {
    if (!runEnabled(opts)) {
      hide()
      return
    }
    const sel = ed.getSelection()
    if (!sel || sel.isEmpty()) {
      hide()
      return
    }
    const text = selectionText(ed)
    if (!isRunnableSelection(text)) {
      hide()
      return
    }
    glyphLine = Math.min(sel.startLineNumber, sel.endLineNumber)
    place()
  }

  const onClick = (ev: MouseEvent) => {
    ev.preventDefault()
    ev.stopPropagation()
    if (!runEnabled(opts) || glyphLine == null) return
    const text = selectionText(ed).trim()
    if (!text || !isRunnableSelection(text)) return
    opts.onRun!(text, { fromSelection: true })
  }
  btn.addEventListener('mousedown', onClick)

  const d1 = ed.onDidChangeCursorSelection(() => refresh())
  const d2 = ed.onDidChangeModelContent(() => refresh())
  const d3 = ed.onDidScrollChange(() => place())
  const d4 = ed.onDidLayoutChange(() => place())

  const disposeAll = {
    dispose: () => {
      btn.removeEventListener('mousedown', onClick)
      btn.remove()
    },
  }

  refresh()
  return [d1, d2, d3, d4, disposeAll]
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Monaco 自研查找会话（对标 IDEA / 成熟 Web IDE）：
 * 不使用内置 Find Widget，避免 Chromium 原生 title="Close (Escape)" 残影。
 */
import type { editor, IRange } from 'monaco-editor'

export type FindOptions = {
  matchCase: boolean
  wholeWord: boolean
  regex: boolean
}

export type FindMatchHit = {
  range: IRange
}

const DECORATION_CURRENT = 'gido-find-current'
const DECORATION_OTHER = 'gido-find-other'

let stylesInjected = false

export function ensureFindDecorationStyles() {
  if (stylesInjected || typeof document === 'undefined') return
  stylesInjected = true
  const el = document.createElement('style')
  el.setAttribute('data-gido-find', '1')
  el.textContent = `
    .monaco-editor .find-widget,
    .monaco-editor .find-widget.visible {
      display: none !important;
      visibility: hidden !important;
      pointer-events: none !important;
    }
    .gido-find-other {
      background-color: rgba(234, 92, 0, 0.33);
    }
    .gido-find-current {
      background-color: rgba(234, 92, 0, 0.45);
      border: 1px solid #cea11d;
      box-sizing: border-box;
    }
  `
  document.head.appendChild(el)
}

export function collectMatches(
  model: editor.ITextModel,
  query: string,
  opts: FindOptions,
  limit = 5000,
): FindMatchHit[] {
  const q = query ?? ''
  if (!q) return []
  try {
    const matches = model.findMatches(
      q,
      false,
      opts.regex,
      opts.matchCase,
      opts.wholeWord ? '`~!@#$%^&*()-=+[{]}\\|;:\'",.<>/?' : null,
      false,
      limit,
    )
    return matches.map(m => ({ range: m.range }))
  } catch {
    return []
  }
}

export function applyFindDecorations(
  ed: editor.IStandaloneCodeEditor,
  hits: FindMatchHit[],
  currentIndex: number,
  decorationIds: string[],
): string[] {
  const decos: editor.IModelDeltaDecoration[] = hits.map((h, i) => ({
    range: h.range,
    options: {
      stickiness: 1,
      className: i === currentIndex ? DECORATION_CURRENT : DECORATION_OTHER,
      overviewRuler: {
        color: i === currentIndex ? 'rgba(255,140,0,0.9)' : 'rgba(255,213,0,0.7)',
        position: 1,
      },
    },
  }))
  return ed.deltaDecorations(decorationIds, decos)
}

export function revealMatch(ed: editor.IStandaloneCodeEditor, hit: FindMatchHit | undefined) {
  if (!hit) return
  ed.setSelection(hit.range)
  ed.revealRangeInCenter(hit.range)
}

export function replaceCurrent(
  ed: editor.IStandaloneCodeEditor,
  hit: FindMatchHit | undefined,
  replacement: string,
): boolean {
  if (!hit) return false
  const model = ed.getModel()
  if (!model) return false
  ed.executeEdits('gido-find-replace', [{
    range: hit.range,
    text: replacement,
    forceMoveMarkers: true,
  }])
  return true
}

export function replaceAll(
  ed: editor.IStandaloneCodeEditor,
  hits: FindMatchHit[],
  replacement: string,
): number {
  if (!hits.length) return 0
  const edits = hits.map(h => ({
    range: h.range,
    text: replacement,
    forceMoveMarkers: true,
  }))
  // 从后往前替换，避免位移
  edits.sort((a, b) => {
    if (a.range.startLineNumber !== b.range.startLineNumber) {
      return b.range.startLineNumber - a.range.startLineNumber
    }
    return b.range.startColumn - a.range.startColumn
  })
  ed.executeEdits('gido-find-replace-all', edits)
  return hits.length
}

export function selectionText(ed: editor.IStandaloneCodeEditor): string {
  const model = ed.getModel()
  if (!model) return ''
  const sel = ed.getSelection()
  if (!sel || sel.isEmpty()) return ''
  return model.getValueInRange(sel)
}

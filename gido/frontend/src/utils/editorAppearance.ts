/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import type { Monaco } from '@monaco-editor/react'

const STORAGE_THEME = 'dw.editor.monaco.theme'
const STORAGE_FONT = 'dw.editor.font'
const STORAGE_SIZE = 'dw.editor.fontSize'

export type EditorFontId = 'jetbrains' | 'fira' | 'sfmono' | 'consolas'

export interface EditorAppearance {
  theme: string
  fontId: EditorFontId
  fontSize: number
}

export const MONACO_THEME_OPTIONS: { value: string; label: string }[] = [
  { value: 'vs', label: '浅色 · VS' },
  { value: 'vs-dark', label: '深色 · VS Dark' },
  { value: 'hc-black', label: '高对比 · HC Black' },
  { value: 'dw-github-dark', label: 'GitHub Dark' },
  { value: 'dw-one-dark', label: 'One Dark' },
  { value: 'dw-solarized-dark', label: 'Solarized Dark' },
  { value: 'dw-dracula', label: 'Dracula' },
]

export const FONT_OPTIONS: { value: EditorFontId; label: string }[] = [
  { value: 'jetbrains', label: 'JetBrains Mono' },
  { value: 'fira', label: 'Fira Code' },
  { value: 'sfmono', label: 'SF Mono / 苹方等宽' },
  { value: 'consolas', label: 'Consolas' },
]

const FONT_STACK: Record<EditorFontId, string> = {
  jetbrains: "'JetBrains Mono','Fira Code',Consolas,'Courier New',monospace",
  fira: "'Fira Code','JetBrains Mono',Consolas,monospace",
  sfmono: "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Monaco,Consolas,monospace",
  consolas: "Consolas,'Courier New',monospace",
}

const DEFAULT_APP: EditorAppearance = {
  theme: 'vs-dark',
  fontId: 'jetbrains',
  fontSize: 14,
}

export function loadEditorAppearance(): EditorAppearance {
  try {
    const theme = localStorage.getItem(STORAGE_THEME) || DEFAULT_APP.theme
    const fontRaw = localStorage.getItem(STORAGE_FONT) as EditorFontId | null
    const fontId = fontRaw && fontRaw in FONT_STACK ? fontRaw : DEFAULT_APP.fontId
    const sizeRaw = Number(localStorage.getItem(STORAGE_SIZE))
    const fontSize = Number.isFinite(sizeRaw) && sizeRaw >= 11 && sizeRaw <= 22 ? sizeRaw : DEFAULT_APP.fontSize
    return { theme, fontId, fontSize }
  } catch {
    return { ...DEFAULT_APP }
  }
}

export function persistEditorAppearance(patch: Partial<EditorAppearance>): EditorAppearance {
  const cur = loadEditorAppearance()
  const next = { ...cur, ...patch }
  try {
    localStorage.setItem(STORAGE_THEME, next.theme)
    localStorage.setItem(STORAGE_FONT, next.fontId)
    localStorage.setItem(STORAGE_SIZE, String(next.fontSize))
  } catch {
    /* ignore */
  }
  return next
}

export function registerDwMonacoThemes(monaco: Monaco) {
  const themes: Record<string, Record<string, string>> = {
    'dw-github-dark': {
      'editor.background': '#0d1117',
      'editor.foreground': '#e6edf3',
      'editorLineNumber.foreground': '#6e7681',
      'editorLineNumber.activeForeground': '#e6edf3',
      'minimap.background': '#0d1117',
    },
    'dw-one-dark': {
      'editor.background': '#282c34',
      'editor.foreground': '#abb2bf',
      'editorLineNumber.foreground': '#495162',
      'editorLineNumber.activeForeground': '#c8ccd4',
      'minimap.background': '#21252b',
    },
    'dw-solarized-dark': {
      'editor.background': '#002b36',
      'editor.foreground': '#839496',
      'editorLineNumber.foreground': '#586e75',
      'editorLineNumber.activeForeground': '#93a1a1',
      'minimap.background': '#00252e',
    },
    'dw-dracula': {
      'editor.background': '#282a36',
      'editor.foreground': '#f8f8f2',
      'editorLineNumber.foreground': '#6272a4',
      'editorLineNumber.activeForeground': '#f8f8f2',
      'minimap.background': '#21222c',
    },
  }
  for (const [name, colors] of Object.entries(themes)) {
    monaco.editor.defineTheme(name, {
      base: 'vs-dark',
      inherit: true,
      rules: [],
      colors,
    })
  }
}

export function monacoEditorOptionsFromAppearance(a: EditorAppearance) {
  return {
    fontFamily: FONT_STACK[a.fontId],
    fontSize: a.fontSize,
    fontLigatures: a.fontId === 'fira' || a.fontId === 'jetbrains',
    minimap: { enabled: false },
    lineNumbers: 'on' as const,
    scrollBeyondLastLine: false,
    automaticLayout: true,
    tabSize: 2,
    wordWrap: 'on' as const,
  }
}

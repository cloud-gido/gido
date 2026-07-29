/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 自研查找条：布局/颜色对齐 Monaco Find Widget，避免原生 Escape tooltip。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Input, Tooltip } from 'antd'
import {
  CloseOutlined,
  DownOutlined,
  UpOutlined,
} from '@ant-design/icons'
import type { editor } from 'monaco-editor'
import type { Monaco } from '@monaco-editor/react'
import {
  applyFindDecorations,
  collectMatches,
  ensureFindDecorationStyles,
  replaceAll,
  replaceCurrent,
  revealMatch,
  selectionText,
  type FindMatchHit,
} from '../utils/monacoCustomFind'
import './monacoFindBar.css'

export type MonacoFindBarApi = {
  openFind: (opts?: { replace?: boolean }) => void
  close: () => void
  isOpen: () => boolean
  next: () => void
  prev: () => void
}

type Props = {
  getEditor: () => editor.IStandaloneCodeEditor | null
  apiRef?: React.MutableRefObject<MonacoFindBarApi | null>
  readOnly?: boolean
  theme?: string
}

/** 近似 VS Code Codicon.replace / replace-all */
function ReplaceIcon() {
  return (
    <svg className="gido-find-codicon" viewBox="0 0 16 16" aria-hidden>
      <path fill="currentColor" d="M3.5 2h4l.35.15 2 2L10 4.5V6H9V4.7L7.3 3H3.5V2zm0 4h3v1h-3V6zm0 2h5v1h-5V8zm7.15-.85 2.5 2.5-.7.7L13 11.2V14h-1v-2.8l-.95.95-.7-.7 2.5-2.5.8-.3zM3.5 10h3v1h-3v-1z" />
    </svg>
  )
}

function ReplaceAllIcon() {
  return (
    <svg className="gido-find-codicon" viewBox="0 0 16 16" aria-hidden>
      <path fill="currentColor" d="M3.5 2h4l.35.15 2 2L10 4.5V5H9V4.7L7.3 3H3.5V2zm0 4h3v1h-3V6zm0 2h5v1h-5V8zm0 2h3v1h-3v-1zm9.15-2.85 1.5 1.5-.7.7-.8-.8V12h-1V8.55l-.8.8-.7-.7 1.5-1.5.5-.2.5.2zm0 3 1.5 1.5-.7.7-.8-.8V15h-1v-3.45l-.8.8-.7-.7 1.5-1.5.5-.2.5.2z" />
    </svg>
  )
}

function isDarkTheme(theme?: string) {
  const t = (theme || '').toLowerCase()
  return t.includes('dark') || t === 'hc-black' || t.startsWith('dw-')
}

export default function MonacoFindBar({ getEditor, apiRef, readOnly, theme }: Props) {
  const [open, setOpen] = useState(false)
  const [replaceMode, setReplaceMode] = useState(false)
  const [query, setQuery] = useState('')
  const [replacement, setReplacement] = useState('')
  const [matchCase, setMatchCase] = useState(false)
  const [wholeWord, setWholeWord] = useState(false)
  const [useRegex, setUseRegex] = useState(false)
  const [hits, setHits] = useState<FindMatchHit[]>([])
  const [index, setIndex] = useState(0)
  const decoRef = useRef<string[]>([])
  const findInputRef = useRef<any>(null)
  const openRef = useRef(false)
  const indexRef = useRef(0)
  const hitsRef = useRef<FindMatchHit[]>([])
  openRef.current = open
  indexRef.current = index
  hitsRef.current = hits

  const dark = isDarkTheme(theme)

  const clearDecos = useCallback(() => {
    const ed = getEditor()
    if (ed) decoRef.current = ed.deltaDecorations(decoRef.current, [])
    else decoRef.current = []
  }, [getEditor])

  const paint = useCallback((nextHits: FindMatchHit[], idx: number) => {
    const ed = getEditor()
    setHits(nextHits)
    setIndex(idx)
    hitsRef.current = nextHits
    indexRef.current = idx
    if (!ed) return
    decoRef.current = applyFindDecorations(ed, nextHits, idx, decoRef.current)
    if (nextHits[idx]) revealMatch(ed, nextHits[idx])
  }, [getEditor])

  const rescan = useCallback((q: string, mc: boolean, ww: boolean, rx: boolean, preferIdx?: number) => {
    const ed = getEditor()
    const model = ed?.getModel()
    if (!ed || !model || !q) {
      paint([], 0)
      if (!q) clearDecos()
      return
    }
    const next = collectMatches(model, q, { matchCase: mc, wholeWord: ww, regex: rx })
    const idx = next.length ? Math.min(Math.max(preferIdx ?? 0, 0), next.length - 1) : 0
    paint(next, idx)
  }, [getEditor, paint, clearDecos])

  const close = useCallback(() => {
    setOpen(false)
    setReplaceMode(false)
    clearDecos()
    try { getEditor()?.focus() } catch { /* ignore */ }
  }, [clearDecos, getEditor])

  const go = useCallback((delta: number) => {
    const list = hitsRef.current
    if (!list.length) return
    const next = (indexRef.current + delta + list.length) % list.length
    paint(list, next)
  }, [paint])

  const openFind = useCallback((o?: { replace?: boolean }) => {
    ensureFindDecorationStyles()
    const ed = getEditor()
    const seed = (ed ? selectionText(ed) : '') || query
    setReplaceMode(Boolean(o?.replace) && !readOnly)
    setOpen(true)
    if (seed && seed !== query) setQuery(seed)
    window.setTimeout(() => {
      findInputRef.current?.focus?.({ cursor: 'all' })
      rescan(seed || query, matchCase, wholeWord, useRegex, 0)
    }, 0)
  }, [getEditor, query, readOnly, matchCase, wholeWord, useRegex, rescan])

  useEffect(() => {
    const api: MonacoFindBarApi = {
      openFind,
      close,
      isOpen: () => openRef.current,
      next: () => go(1),
      prev: () => go(-1),
    }
    if (apiRef) apiRef.current = api
    return () => { if (apiRef) apiRef.current = null }
  }, [apiRef, openFind, close, go])

  useEffect(() => {
    if (!open) return
    rescan(query, matchCase, wholeWord, useRegex, indexRef.current)
  }, [query, matchCase, wholeWord, useRegex, open, rescan])

  useEffect(() => () => clearDecos(), [clearDecos])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (!openRef.current) return
      e.preventDefault()
      e.stopPropagation()
      close()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [close])

  if (!open) return null

  const countLabel = !query
    ? ''
    : hits.length === 0
      ? 'No results'
      : `${index + 1} of ${hits.length}`

  const toggle = (active: boolean, label: React.ReactNode, title: string, onClick: () => void) => (
    <Tooltip title={title} mouseEnterDelay={0.55}>
      <button
        type="button"
        className={`gido-find-toggle${active ? ' is-active' : ''}`}
        aria-pressed={active}
        aria-label={title}
        onClick={onClick}
      >
        {label}
      </button>
    </Tooltip>
  )

  const iconBtn = (title: string, onClick: () => void, icon: React.ReactNode, disabled?: boolean) => (
    <Tooltip title={title} mouseEnterDelay={0.55}>
      <button
        type="button"
        className="gido-find-icon-btn"
        aria-label={title}
        disabled={disabled}
        onClick={onClick}
      >
        {icon}
      </button>
    </Tooltip>
  )

  const doReplaceOne = () => {
    const ed = getEditor()
    if (!ed || readOnly) return
    replaceCurrent(ed, hits[index], replacement)
    window.setTimeout(() => rescan(query, matchCase, wholeWord, useRegex, index), 0)
  }
  const doReplaceAll = () => {
    const ed = getEditor()
    if (!ed || readOnly) return
    replaceAll(ed, hits, replacement)
    window.setTimeout(() => rescan(query, matchCase, wholeWord, useRegex, 0), 0)
  }

  return (
    <div
      className={`gido-monaco-find-bar${dark ? ' is-dark' : ' is-light'}${replaceMode ? ' is-replace' : ''}`}
      role="search"
      onMouseDown={e => e.stopPropagation()}
    >
      {!readOnly && (
        <Tooltip title={replaceMode ? 'Hide Replace' : 'Toggle Replace'} mouseEnterDelay={0.55}>
          <button
            type="button"
            className="gido-find-expand"
            aria-expanded={replaceMode}
            aria-label={replaceMode ? 'Hide Replace' : 'Toggle Replace'}
            onClick={() => setReplaceMode(v => !v)}
          >
            <DownOutlined className="gido-find-chevron" />
          </button>
        </Tooltip>
      )}
      <Tooltip title="Close" mouseEnterDelay={0.55}>
        <button type="button" className="gido-find-close" aria-label="Close" onClick={close}>
          <CloseOutlined style={{ fontSize: 11 }} />
        </button>
      </Tooltip>

      <div className="gido-find-part">
        <div className="gido-find-input-wrap">
          <Input
            ref={findInputRef}
            size="small"
            className="gido-find-input"
            placeholder="Find"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onPressEnter={e => { if (e.shiftKey) go(-1); else go(1) }}
            variant="borderless"
          />
          <div className="gido-find-toggles">
            {toggle(matchCase, 'Aa', 'Match Case', () => setMatchCase(v => !v))}
            {toggle(wholeWord, <span className="gido-find-whole">ab</span>, 'Match Whole Word', () => setWholeWord(v => !v))}
            {toggle(useRegex, '.*', 'Use Regular Expression', () => setUseRegex(v => !v))}
          </div>
        </div>
        <span className={`gido-find-matches${hits.length === 0 && query ? ' is-empty' : ''}`}>
          {countLabel}
        </span>
        <div className="gido-find-actions">
          {iconBtn('Previous Match', () => go(-1), <UpOutlined style={{ fontSize: 11 }} />, !hits.length)}
          {iconBtn('Next Match', () => go(1), <DownOutlined style={{ fontSize: 11 }} />, !hits.length)}
        </div>
      </div>

      {replaceMode && !readOnly && (
        <div className="gido-find-part gido-replace-part">
          <div className="gido-find-input-wrap">
            <Input
              size="small"
              className="gido-find-input"
              placeholder="Replace"
              value={replacement}
              onChange={e => setReplacement(e.target.value)}
              onPressEnter={doReplaceOne}
              variant="borderless"
            />
          </div>
          <span className="gido-find-matches gido-find-matches-pad" aria-hidden />
          <div className="gido-find-actions">
            {iconBtn('Replace', doReplaceOne, <ReplaceIcon />, !hits.length)}
            {iconBtn('Replace All', doReplaceAll, <ReplaceAllIcon />, !hits.length)}
          </div>
        </div>
      )}
    </div>
  )
}

export function bindMonacoFindKeybindings(
  ed: editor.IStandaloneCodeEditor,
  monaco: Monaco,
  getApi: () => MonacoFindBarApi | null,
): void {
  ensureFindDecorationStyles()
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyF, () => {
    getApi()?.openFind({ replace: false })
  })
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyH, () => {
    getApi()?.openFind({ replace: true })
  })
  ed.addCommand(monaco.KeyCode.F3, () => {
    const a = getApi()
    if (!a) return
    if (!a.isOpen()) a.openFind({ replace: false })
    else a.next()
  })
  ed.addCommand(monaco.KeyMod.Shift | monaco.KeyCode.F3, () => {
    const a = getApi()
    if (!a) return
    if (!a.isOpen()) a.openFind({ replace: false })
    else a.prev()
  })
}

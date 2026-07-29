/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 自研查找/替换条：交互对齐 Monaco 原 Find Widget（位置、展开替换、计数文案、开关），
 * 控件用 Ant Design，避免浏览器原生 title 残影。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Input, Tooltip } from 'antd'
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
  /** Monaco theme id，用于条与编辑器明暗一致 */
  theme?: string
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

  // 与 Monaco 文案对齐：No results / 1 of N
  const countLabel = !query
    ? ''
    : hits.length === 0
      ? 'No results'
      : `${index + 1} of ${hits.length}`

  const toggle = (active: boolean, label: string, title: string, onClick: () => void) => (
    <Tooltip title={title} mouseEnterDelay={0.5}>
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
    <Tooltip title={title} mouseEnterDelay={0.5}>
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
      className={`gido-monaco-find-bar${dark ? ' is-dark' : ' is-light'}`}
      role="search"
      onMouseDown={e => e.stopPropagation()}
    >
      <div className="gido-find-row">
        {!readOnly && (
          <Tooltip title={replaceMode ? '隐藏替换' : '切换替换'} mouseEnterDelay={0.5}>
            <button
              type="button"
              className="gido-find-icon-btn gido-find-expand"
              aria-label={replaceMode ? '隐藏替换' : '切换替换'}
              aria-expanded={replaceMode}
              onClick={() => setReplaceMode(v => !v)}
            >
              {replaceMode ? <DownOutlined style={{ fontSize: 10, transform: 'rotate(180deg)' }} /> : <DownOutlined style={{ fontSize: 10 }} />}
            </button>
          </Tooltip>
        )}
        <Input
          ref={findInputRef}
          size="small"
          className="gido-find-input"
          placeholder="Find"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onPressEnter={e => { if (e.shiftKey) go(-1); else go(1) }}
          allowClear
        />
        <div className="gido-find-toggles">
          {toggle(matchCase, 'Aa', 'Match Case', () => setMatchCase(v => !v))}
          {toggle(wholeWord, 'ab', 'Match Whole Word', () => setWholeWord(v => !v))}
          {toggle(useRegex, '.*', 'Use Regular Expression', () => setUseRegex(v => !v))}
        </div>
        <span className={`gido-find-matches${hits.length === 0 && query ? ' is-empty' : ''}`}>
          {countLabel}
        </span>
        {iconBtn('Previous Match', () => go(-1), <UpOutlined />, !hits.length)}
        {iconBtn('Next Match', () => go(1), <DownOutlined />, !hits.length)}
        {iconBtn('Close', close, <CloseOutlined />)}
      </div>
      {replaceMode && !readOnly && (
        <div className="gido-find-row gido-find-replace-row">
          <span className="gido-find-expand-spacer" />
          <Input
            size="small"
            className="gido-find-input"
            placeholder="Replace"
            value={replacement}
            onChange={e => setReplacement(e.target.value)}
            onPressEnter={doReplaceOne}
          />
          <Button size="small" className="gido-find-text-btn" disabled={!hits.length} onClick={doReplaceOne}>
            Replace
          </Button>
          <Button size="small" className="gido-find-text-btn" disabled={!hits.length} onClick={doReplaceAll}>
            Replace All
          </Button>
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

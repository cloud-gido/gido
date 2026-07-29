/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 自研查找/替换条（对标 IDEA）：Ant Design + Monaco API，禁用内置 Find Widget。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Input, Space, Tooltip } from 'antd'
import { CloseOutlined, DownOutlined, UpOutlined } from '@ant-design/icons'
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
}

export default function MonacoFindBar({ getEditor, apiRef, readOnly }: Props) {
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

  const countLabel = !query ? '' : hits.length === 0 ? '无结果' : `${index + 1} / ${hits.length}`

  const toggleBtn = (active: boolean, label: string, title: string, onClick: () => void) => (
    <Tooltip title={title} mouseEnterDelay={0.4}>
      <Button type={active ? 'primary' : 'text'} size="small" onClick={onClick} style={{ minWidth: 28, fontWeight: 600, fontSize: 12 }}>
        {label}
      </Button>
    </Tooltip>
  )

  return (
    <div
      className="gido-monaco-find-bar"
      style={{
        position: 'absolute',
        top: 8,
        right: 16,
        zIndex: 20,
        background: '#fff',
        border: '1px solid #e8ecf2',
        borderRadius: 8,
        boxShadow: '0 4px 16px rgba(15,23,42,0.12)',
        padding: '8px 10px',
        minWidth: 360,
        maxWidth: 'min(480px, calc(100% - 24px))',
      }}
      onMouseDown={e => e.stopPropagation()}
    >
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            ref={findInputRef}
            size="small"
            placeholder="查找"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onPressEnter={e => { if (e.shiftKey) go(-1); else go(1) }}
            allowClear
            style={{ flex: 1 }}
          />
          {toggleBtn(matchCase, 'Aa', '区分大小写', () => setMatchCase(v => !v))}
          {toggleBtn(wholeWord, 'W', '全词匹配', () => setWholeWord(v => !v))}
          {toggleBtn(useRegex, '.*', '正则表达式', () => setUseRegex(v => !v))}
          <span style={{ fontSize: 12, color: '#8c8c8c', minWidth: 56, textAlign: 'center', lineHeight: '24px' }}>{countLabel}</span>
          <Tooltip title="上一个">
            <Button size="small" icon={<UpOutlined />} onClick={() => go(-1)} disabled={!hits.length} />
          </Tooltip>
          <Tooltip title="下一个">
            <Button size="small" icon={<DownOutlined />} onClick={() => go(1)} disabled={!hits.length} />
          </Tooltip>
          {!readOnly && (
            <Tooltip title={replaceMode ? '收起替换' : '替换'}>
              <Button size="small" type="text" onClick={() => setReplaceMode(v => !v)} style={{ fontSize: 12 }}>
                {replaceMode ? '∧' : '∨'}
              </Button>
            </Tooltip>
          )}
          <Tooltip title="关闭 (Esc)" mouseEnterDelay={0.4}>
            <Button size="small" type="text" icon={<CloseOutlined />} onClick={close} />
          </Tooltip>
        </Space.Compact>
        {replaceMode && !readOnly && (
          <Space.Compact style={{ width: '100%' }}>
            <Input
              size="small"
              placeholder="替换为"
              value={replacement}
              onChange={e => setReplacement(e.target.value)}
              onPressEnter={() => {
                const ed = getEditor()
                if (!ed || readOnly) return
                replaceCurrent(ed, hits[index], replacement)
                window.setTimeout(() => rescan(query, matchCase, wholeWord, useRegex, index), 0)
              }}
              style={{ flex: 1 }}
            />
            <Button
              size="small"
              disabled={!hits.length}
              onClick={() => {
                const ed = getEditor()
                if (!ed) return
                replaceCurrent(ed, hits[index], replacement)
                window.setTimeout(() => rescan(query, matchCase, wholeWord, useRegex, index), 0)
              }}
            >
              替换
            </Button>
            <Button
              size="small"
              disabled={!hits.length}
              onClick={() => {
                const ed = getEditor()
                if (!ed) return
                replaceAll(ed, hits, replacement)
                window.setTimeout(() => rescan(query, matchCase, wholeWord, useRegex, 0), 0)
              }}
            >
              全部替换
            </Button>
          </Space.Compact>
        )}
      </Space>
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

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, isValidElement } from 'react'
import { Table, message } from 'antd'
import type { ColumnType, ColumnsType } from 'antd/es/table'
import type { QueryRowRec } from './QueryResultTable'
import { queryResultTableComponents } from './QueryResultTable'
import { formatCellDisplay } from '../utils/cellDisplay'
import './queryResultPanel.css'

export function formatQueryCellValue(v: unknown): string {
  return formatCellDisplay(v, 0)
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      return true
    } catch {
      return false
    }
  }
}

type CtxMenu = { x: number; y: number; cellText: string; tsvText: string }

type Props = {
  columns: ColumnsType<QueryRowRec>
  dataSource: QueryRowRec[]
  toolbar?: ReactNode
  empty?: ReactNode
}

/**
 * GIDO Batch 风格结果表：
 * - 单滚动视口（表头 sticky，横向与数据始终对齐）
 * - 底部固定横滚条、右侧固定纵滚条（与主区双向同步）
 */
export default function QueryResultPanel({ columns, dataSource, toolbar, empty }: Props) {
  const mainRef = useRef<HTMLDivElement>(null)
  const hTrackRef = useRef<HTMLDivElement>(null)
  const hInnerRef = useRef<HTMLDivElement>(null)
  const vTrackRef = useRef<HTMLDivElement>(null)
  const vInnerRef = useRef<HTMLDivElement>(null)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [ctx, setCtx] = useState<CtxMenu | null>(null)

  const tableMinWidth = useMemo(() => {
    let w = 40
    for (const c of columns) {
      w += typeof c.width === 'number' ? c.width : 148
    }
    return w
  }, [columns])

  const syncScrollbarSizes = useCallback(() => {
    const main = mainRef.current
    const hInner = hInnerRef.current
    const vInner = vInnerRef.current
    if (!main || !hInner || !vInner) return
    hInner.style.width = `${main.scrollWidth}px`
    vInner.style.height = `${main.scrollHeight}px`
  }, [])

  const bindScrollSync = useCallback(() => {
    const main = mainRef.current
    const hTrack = hTrackRef.current
    const vTrack = vTrackRef.current
    if (!main || !hTrack || !vTrack) return () => {}

    syncScrollbarSizes()

    let syncing = false
    const apply = (left: number, top: number) => {
      main.scrollLeft = left
      main.scrollTop = top
      hTrack.scrollLeft = left
      vTrack.scrollTop = top
    }

    const fromMain = () => {
      if (syncing) return
      syncing = true
      hTrack.scrollLeft = main.scrollLeft
      vTrack.scrollTop = main.scrollTop
      syncing = false
    }
    const fromH = () => {
      if (syncing) return
      syncing = true
      apply(hTrack.scrollLeft, main.scrollTop)
      syncing = false
    }
    const fromV = () => {
      if (syncing) return
      syncing = true
      apply(main.scrollLeft, vTrack.scrollTop)
      syncing = false
    }

    main.addEventListener('scroll', fromMain, { passive: true })
    hTrack.addEventListener('scroll', fromH, { passive: true })
    vTrack.addEventListener('scroll', fromV, { passive: true })

    const ro = new ResizeObserver(() => syncScrollbarSizes())
    ro.observe(main)
    const tableEl = main.querySelector('.ant-table')
    if (tableEl) ro.observe(tableEl)

    return () => {
      main.removeEventListener('scroll', fromMain)
      hTrack.removeEventListener('scroll', fromH)
      vTrack.removeEventListener('scroll', fromV)
      ro.disconnect()
    }
  }, [syncScrollbarSizes])

  useEffect(() => {
    let unbind: (() => void) | undefined
    const t = window.setTimeout(() => {
      syncScrollbarSizes()
      unbind = bindScrollSync()
    }, 0)
    return () => {
      window.clearTimeout(t)
      unbind?.()
    }
  }, [bindScrollSync, syncScrollbarSizes, dataSource, columns, tableMinWidth])

  useEffect(() => {
    if (!ctx) return
    const close = () => setCtx(null)
    window.addEventListener('click', close)
    window.addEventListener('scroll', close, true)
    return () => {
      window.removeEventListener('click', close)
      window.removeEventListener('scroll', close, true)
    }
  }, [ctx])

  const doCopy = useCallback(async (text: string, hint?: string) => {
    const ok = await copyToClipboard(text)
    if (ok) message.success(hint ?? '已复制到剪贴板')
    else message.error('复制失败，请手动选择后 ⌘C')
    setCtx(null)
  }, [])

  const columnsWithCopy = useMemo((): ColumnsType<QueryRowRec> => {
    return columns.map(col => {
      if ('children' in col && col.children) return col
      const leaf = col as ColumnType<QueryRowRec>
      const field = String(leaf.dataIndex ?? leaf.key ?? '')
      const origRender = leaf.render
      return {
        ...leaf,
        render: (value: unknown, record: QueryRowRec, index: number) => {
          const text = formatQueryCellValue(value)
          const cellKey = `${index}:${field}`
          let inner: ReactNode
          if (origRender) {
            const rendered = origRender(value, record, index)
            if (isValidElement(rendered) || typeof rendered === 'string' || typeof rendered === 'number') {
              inner = rendered as ReactNode
            } else {
              inner = <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>
            }
          } else if (value === null || value === 'None') {
            inner = <span style={{ color: '#bfbfbf' }}>NULL</span>
          } else {
            inner = <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>
          }
          return (
            <div
              className={`dw-cell-value${copiedKey === cellKey ? ' dw-cell-value--copied' : ''}`}
              title="双击复制；右键更多选项；可拖选后 ⌘C"
              onDoubleClick={e => {
                e.stopPropagation()
                void doCopy(text, '已复制单元格')
                setCopiedKey(cellKey)
                window.setTimeout(() => setCopiedKey(k => (k === cellKey ? null : k)), 600)
              }}
              onContextMenu={e => {
                e.preventDefault()
                e.stopPropagation()
                const tsv = `${field}\t${text}`
                setCtx({ x: e.clientX, y: e.clientY, cellText: text, tsvText: tsv })
              }}
            >
              {inner}
            </div>
          )
        },
      }
    })
  }, [columns, copiedKey, doCopy])

  if (!dataSource.length && empty) {
    return <div className="dw-query-result">{empty}</div>
  }

  return (
    <div className="dw-query-result">
      {toolbar ? <div className="dw-query-result__toolbar">{toolbar}</div> : null}
      <div className="dw-query-result__viewport">
        <div ref={mainRef} className="dw-query-result__main" title="滚轮滚动；表头随横向滚动对齐">
          <Table
            size="small"
            rowKey="_key"
            columns={columnsWithCopy}
            dataSource={dataSource}
            pagination={false}
            tableLayout="fixed"
            style={{ minWidth: tableMinWidth }}
            components={queryResultTableComponents}
          />
        </div>
        <div ref={vTrackRef} className="dw-query-result__vscroll" title="纵向滚动">
          <div ref={vInnerRef} className="dw-query-result__vscroll-inner" />
        </div>
        <div ref={hTrackRef} className="dw-query-result__hscroll" title="横向滚动（表头与数据同步）">
          <div ref={hInnerRef} className="dw-query-result__hscroll-inner" />
        </div>
        <div className="dw-query-result__corner" aria-hidden />
      </div>
      {ctx && (
        <div
          className="dw-query-result__ctx"
          style={{ left: ctx.x, top: ctx.y }}
          onClick={e => e.stopPropagation()}
        >
          <button type="button" onClick={() => void doCopy(ctx.cellText, '已复制单元格')}>
            复制单元格
          </button>
          <button type="button" onClick={() => void doCopy(ctx.tsvText, '已复制（列名 + 制表符 + 值）')}>
            复制为 TSV
          </button>
        </div>
      )}
    </div>
  )
}

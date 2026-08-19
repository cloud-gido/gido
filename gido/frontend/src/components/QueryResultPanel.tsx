/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, isValidElement, startTransition } from 'react'
import { Pagination, Table, message, Descriptions, Select } from 'antd'
import type { ColumnType, ColumnsType, SorterResult } from 'antd/es/table/interface'
import type { TableProps } from 'antd'
import type { QueryRowRec } from './QueryResultTable'
import { queryResultTableComponents } from './QueryResultTable'
import { formatCellDisplay } from '../utils/cellDisplay'
import { sortQueryRows, queryResultDataFingerprint, type QuerySortOrder } from '../utils/queryCellSort'
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
  /** 客户端分页；默认每页 100，避免大结果一次渲染卡死页面。传 false 关闭。 */
  pagination?: false | {
    pageSize?: number
    pageSizeOptions?: string[]
  }
  /**
   * 展示模式：
   * - `table`：原来的表格视图（默认）
   * - `kv`：行转列（类似 DBeaver），仅展示指定行（默认首行）
   */
  viewMode?: 'table' | 'kv'
  kvRowIndex?: number
  /**
   * 是否在结果面板内显示“表格/行转列”切换。
   * - 若外部传入 `viewMode`：此开关仅用于视觉展示，不会改变 mode（可继续传入看起来统一）
   * - 若未传入 `viewMode`：面板内使用内部状态切换
   */
  showViewModeToggle?: boolean
  /** 视图模式持久化（sessionStorage）；不传则仅当前会话生效 */
  viewModeStorageKey?: string
}

/**
 * GIDO Batch 风格结果表：
 * - 单滚动视口（表头 sticky，横向与数据始终对齐）
 * - 底部固定横滚条、右侧固定纵滚条（与主区双向同步）
 */
export default function QueryResultPanel({
  columns,
  dataSource,
  toolbar,
  empty,
  pagination,
  viewMode,
  kvRowIndex = 0,
  showViewModeToggle = false,
  viewModeStorageKey,
}: Props) {
  const [internalViewMode, setInternalViewMode] = useState<'table' | 'kv'>('table')

  useEffect(() => {
    if (!viewModeStorageKey) return
    try {
      const raw = sessionStorage.getItem(viewModeStorageKey)
      if (raw === 'table' || raw === 'kv') setInternalViewMode(raw)
    } catch {
      /* ignore */
    }
  }, [viewModeStorageKey])

  const currentViewMode: 'table' | 'kv' = viewMode ?? internalViewMode

  const viewModeToggle = showViewModeToggle ? (
    <Select
      size="small"
      style={{ width: 180 }}
      value={currentViewMode}
      options={[
        { value: 'table', label: '表格' },
        { value: 'kv', label: '行转列（首行）' },
      ]}
      onChange={v => {
        const next = v as 'table' | 'kv'
        if (viewMode == null) setInternalViewMode(next)
        if (viewModeStorageKey) {
          try {
            sessionStorage.setItem(viewModeStorageKey, next)
          } catch {
            /* ignore */
          }
        }
      }}
    />
  ) : null

  if (currentViewMode === 'kv') {
    const row = dataSource[kvRowIndex] ?? dataSource[0]
    const leafKeys = columns
      .filter(c => !('children' in (c as any)))
      .map(c => String((c as any).dataIndex ?? (c as any).key ?? ''))
      .filter(Boolean)

    return (
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {toolbar || viewModeToggle ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', padding: '8px 12px' }}>
            {toolbar}
            {viewModeToggle}
          </div>
        ) : null}
        <div style={{ padding: 12 }}>
          {row && leafKeys.length ? (
            <Descriptions size="small" bordered column={1}>
              {leafKeys.map((k, idx) => (
                <Descriptions.Item key={`${k}:${idx}`} label={k}>
                  <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
                    {formatQueryCellValue((row as any)[k])}
                  </span>
                </Descriptions.Item>
              ))}
            </Descriptions>
          ) : (
            <div style={{ color: '#999', fontSize: 13 }}>{empty ?? '无数据'}</div>
          )}
        </div>
      </div>
    )
  }

  const mainRef = useRef<HTMLDivElement>(null)
  const hTrackRef = useRef<HTMLDivElement>(null)
  const hInnerRef = useRef<HTMLDivElement>(null)
  const vTrackRef = useRef<HTMLDivElement>(null)
  const vInnerRef = useRef<HTMLDivElement>(null)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [ctx, setCtx] = useState<CtxMenu | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(pagination === false ? 100 : (pagination?.pageSize ?? 100))
  /** 全量结果排序后再分页（升序 → 降序 → 取消） */
  const [sort, setSort] = useState<{ field: string; order: QuerySortOrder } | null>(null)

  /** 同列升序结果缓存：切降序时 O(n) reverse，避免再排一遍 */
  const ascendCacheRef = useRef<{ field: string; rows: QueryRowRec[]; fp: string } | null>(null)
  const dataFingerprint = useMemo(() => queryResultDataFingerprint(dataSource), [dataSource])

  useEffect(() => {
    setPage(1)
    setSort(null)
    ascendCacheRef.current = null
  }, [dataFingerprint])

  const pagingEnabled = pagination !== false
  const pageSizeOptions = pagination === false
    ? []
    : (pagination?.pageSizeOptions ?? ['50', '100', '200', '500'])

  const sortedData = useMemo(() => {
    if (!sort?.field || !sort.order) return dataSource
    const cached = ascendCacheRef.current
    let ascendRows: QueryRowRec[]
    if (cached && cached.field === sort.field && cached.fp === dataFingerprint) {
      ascendRows = cached.rows
    } else {
      ascendRows = sortQueryRows(dataSource, sort.field, 'ascend')
      ascendCacheRef.current = { field: sort.field, rows: ascendRows, fp: dataFingerprint }
    }
    if (sort.order === 'ascend') return ascendRows
    // 降序 = 升序结果倒序（同列二次点击几乎零成本）
    const desc = new Array<QueryRowRec>(ascendRows.length)
    for (let i = 0, j = ascendRows.length - 1; j >= 0; i++, j--) desc[i] = ascendRows[j]
    return desc
  }, [dataSource, dataFingerprint, sort])

  const pagedData = useMemo(() => {
    if (!pagingEnabled) return sortedData
    const start = (page - 1) * pageSize
    return sortedData.slice(start, start + pageSize)
  }, [sortedData, pagingEnabled, page, pageSize])

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
  }, [bindScrollSync, syncScrollbarSizes, pagedData, columns, tableMinWidth])

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
        // 排序在面板内对全量数据完成；compare 恒为 0，避免再对当前页二次排序打乱结果
        sorter: field ? { compare: () => 0 } : undefined,
        sortOrder: sort && field && sort.field === field ? sort.order : null,
        sortDirections: ['ascend', 'descend'] as const,
        showSorterTooltip: { title: '点击升序 · 再点降序 · 再点取消' },
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
  }, [columns, copiedKey, doCopy, sort])

  const onTableChange: TableProps<QueryRowRec>['onChange'] = useCallback((_pag, _filters, sorter) => {
    const s = (Array.isArray(sorter) ? sorter[0] : sorter) as SorterResult<QueryRowRec>
    const field = s?.field != null ? String(s.field) : (s?.columnKey != null ? String(s.columnKey) : '')
    const order = s?.order
    // startTransition：大结果排序不阻塞点击反馈，降低「点一下卡死」感
    startTransition(() => {
      if (field && (order === 'ascend' || order === 'descend')) {
        setSort({ field, order })
        setPage(1)
      } else {
        setSort(null)
      }
    })
  }, [])

  if (!dataSource.length && empty) {
    return <div className="dw-query-result">{empty}</div>
  }

  return (
    <div className="dw-query-result">
      {(toolbar || viewModeToggle) ? (
        <div className="dw-query-result__toolbar" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {toolbar}
          {viewModeToggle}
        </div>
      ) : null}
      <div className="dw-query-result__viewport">
        <div ref={mainRef} className="dw-query-result__main" title="滚轮滚动；表头随横向滚动对齐">
          <Table
            size="small"
            rowKey="_key"
            columns={columnsWithCopy}
            dataSource={pagedData}
            pagination={false}
            tableLayout="fixed"
            style={{ minWidth: tableMinWidth }}
            components={queryResultTableComponents}
            onChange={onTableChange}
            showSorterTooltip={{ title: '点击升序 · 再点降序 · 再点取消' }}
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
      {pagingEnabled && dataSource.length > 0 && (
        <div className="dw-query-result__pager">
          <Pagination
            size="small"
            current={page}
            pageSize={pageSize}
            total={sortedData.length}
            showSizeChanger
            pageSizeOptions={pageSizeOptions}
            showTotal={(total, range) => `${range[0]}-${range[1]} / ${total} 行`}
            onChange={(p, ps) => {
              setPage(p)
              setPageSize(ps)
            }}
          />
        </div>
      )}
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

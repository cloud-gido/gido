/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, isValidElement, startTransition } from 'react'
import { Pagination, Table, message, Descriptions } from 'antd'
import { TableOutlined } from '@ant-design/icons'
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
  /** 保留向后兼容，不再影响渲染逻辑。 @deprecated */
  viewMode?: 'table' | 'kv'
  /** 保留向后兼容。 @deprecated */
  kvRowIndex?: number
  /** 保留向后兼容。 @deprecated */
  showViewModeToggle?: boolean
  /** 保留向后兼容。 @deprecated */
  viewModeStorageKey?: string
}

/**
 * GIDO 查询结果面板（DBeaver 风格）：
 * - 主表格左侧有行号列（#），点击行号 → 底部展开该行的列-值详情，两区域同时可见
 * - 单滚动视口，表头 sticky，底横滚 / 右纵滚固定
 */
export default function QueryResultPanel({
  columns,
  dataSource,
  toolbar,
  empty,
  pagination,
}: Props) {
  /** 当前展开行的 _key；null = 未选中，不显示 KV 面板 */
  const [kvKey, setKvKey] = useState<number | null>(null)
  const [kvHeight, setKvHeight] = useState<number>(240)
  const kvHeightClamp = useMemo(() => ({ min: 120, max: 420 }), [])
  const resizingKvRef = useRef(false)

  useEffect(() => {
    setKvKey(null)
  }, [dataSource])

  const startKvResize = useCallback(
    (e: any) => {
      if (e.button !== 0) return
      e.preventDefault()
      e.stopPropagation()
      const startY = e.clientY
      const startH = kvHeight
      resizingKvRef.current = true

      const onMove = (ev: MouseEvent) => {
        if (!resizingKvRef.current) return
        const dy = ev.clientY - startY
        const next = Math.max(kvHeightClamp.min, Math.min(kvHeightClamp.max, startH + dy))
        setKvHeight(next)
      }

      const onUp = () => {
        resizingKvRef.current = false
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
      }

      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [kvHeight, kvHeightClamp],
  )

  const mainRef = useRef<HTMLDivElement>(null)
  const hTrackRef = useRef<HTMLDivElement>(null)
  const hInnerRef = useRef<HTMLDivElement>(null)
  const vTrackRef = useRef<HTMLDivElement>(null)
  const vInnerRef = useRef<HTMLDivElement>(null)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [ctx, setCtx] = useState<CtxMenu | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(pagination === false ? 100 : (pagination?.pageSize ?? 100))
  const [sort, setSort] = useState<{ field: string; order: QuerySortOrder } | null>(null)

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
    let w = 40 + 44 // 行号列 44px
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

  /** 行号列：点击切换 KV 展开；再次点击同一行关闭 */
  const rowNumColumn: ColumnType<QueryRowRec> = useMemo(() => ({
    key: '__rownum__',
    dataIndex: '__rownum__',
    title: (
      <span
        className="dw-rownum-header"
        title="点击行号展开该行详情（类似 DBeaver）"
      >
        <TableOutlined style={{ fontSize: 11 }} />
      </span>
    ),
    width: 44,
    fixed: 'left' as const,
    render: (_: unknown, record: QueryRowRec, index: number) => {
      const k = (record as any)._key as number
      const active = kvKey === k
      const displayNum = (page - 1) * pageSize + index + 1
      return (
        <button
          type="button"
          className={`dw-rownum-btn${active ? ' dw-rownum-btn--active' : ''}`}
          title={active ? '点击收起详情' : '点击展开该行详情'}
          onClick={e => {
            e.stopPropagation()
            setKvKey(prev => (prev === k ? null : k))
          }}
        >
          {displayNum}
        </button>
      )
    },
  }), [kvKey, page, pageSize])

  const allColumns = useMemo(
    () => [rowNumColumn, ...columnsWithCopy],
    [rowNumColumn, columnsWithCopy],
  )

  const onTableChange: TableProps<QueryRowRec>['onChange'] = useCallback((_pag, _filters, sorter) => {
    const s = (Array.isArray(sorter) ? sorter[0] : sorter) as SorterResult<QueryRowRec>
    const field = s?.field != null ? String(s.field) : (s?.columnKey != null ? String(s.columnKey) : '')
    const order = s?.order
    startTransition(() => {
      if (field && (order === 'ascend' || order === 'descend')) {
        setSort({ field, order })
        setPage(1)
      } else {
        setSort(null)
      }
    })
  }, [])

  /** 当前选中行的数据 */
  const kvRowData = useMemo(
    () => kvKey != null ? dataSource.find(r => (r as any)._key === kvKey) ?? null : null,
    [dataSource, kvKey],
  )

  const leafKeys = useMemo(
    () => columns
      .filter(c => !('children' in (c as any)))
      .map(c => String((c as any).dataIndex ?? (c as any).key ?? ''))
      .filter(Boolean),
    [columns],
  )

  if (!dataSource.length && empty) {
    return <div className="dw-query-result">{empty}</div>
  }

  return (
    <div className="dw-query-result">
      {toolbar ? (
        <div className="dw-query-result__toolbar" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {toolbar}
        </div>
      ) : null}
      <div className="dw-query-result__viewport">
        <div ref={mainRef} className="dw-query-result__main" title="滚轮滚动；表头随横向滚动对齐">
          <Table
            size="small"
            rowKey="_key"
            columns={allColumns}
            dataSource={pagedData}
            pagination={false}
            tableLayout="fixed"
            style={{ minWidth: tableMinWidth }}
            components={queryResultTableComponents}
            onChange={onTableChange}
            rowClassName={record => {
              const k = (record as any)._key as number
              return k === kvKey ? 'dw-row--selected' : ''
            }}
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
      {/* KV 详情面板：点击行号后在底部展开，两区域同时可见（DBeaver 风格） */}
      {kvRowData && (
        <div className="dw-query-result__kv" style={{ height: kvHeight }}>
          <div className="dw-query-result__kv-header">
            <span>行详情</span>
            <button
              type="button"
              className="dw-query-result__kv-close"
              onClick={() => setKvKey(null)}
              title="关闭详情"
            >
              ✕
            </button>
          </div>
          <div className="dw-query-result__kv-resize-handle" onMouseDown={startKvResize} title="拖拽调整详情面板高度" />
          <div className="dw-query-result__kv-body">
            <Descriptions size="small" bordered column={1}>
              {leafKeys.map((k, idx) => (
                <Descriptions.Item key={`${k}:${idx}`} label={k}>
                  <span
                    style={{ fontFamily: 'monospace', fontSize: 12, cursor: 'text', userSelect: 'text', wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}
                    title="可拖选复制"
                  >
                    {kvRowData[k] === null || kvRowData[k] === undefined
                      ? <span style={{ color: '#bfbfbf' }}>NULL</span>
                      : formatQueryCellValue(kvRowData[k])
                    }
                  </span>
                </Descriptions.Item>
              ))}
            </Descriptions>
          </div>
        </div>
      )}
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

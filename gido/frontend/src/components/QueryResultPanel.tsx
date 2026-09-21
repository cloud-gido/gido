/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode, isValidElement, startTransition } from 'react'
import { Button, Dropdown, Input, Pagination, Table, Tooltip, message, Descriptions } from 'antd'
import {
  BarChartOutlined,
  ColumnWidthOutlined,
  SearchOutlined,
  TableOutlined,
} from '@ant-design/icons'
import type { ColumnType, ColumnsType, FilterValue, SorterResult } from 'antd/es/table/interface'
import type { TableProps } from 'antd'
import type { QueryRowRec } from './QueryResultTable'
import { queryResultTableComponents } from './QueryResultTable'
import QueryResultChartDrawer from './QueryResultChartDrawer'
import type { QueryChartField } from '../utils/queryResultChart'
import { formatCellDisplay } from '../utils/cellDisplay'
import { sortQueryRows, queryResultDataFingerprint, type QuerySortOrder } from '../utils/queryCellSort'
import { shouldSuppressHeaderInteraction } from '../utils/columnResizeGesture'
import {
  computeQueryColumnPageStats,
  computeQueryResultSelectionAggregates,
  findQueryResultMatches,
  formatQueryResultAggregateNumber,
  formatQueryResultSelectionTsv,
  isQueryResultCellSelected,
  moveQueryResultFocus,
  normalizeQueryResultSelection,
  queryResultSelectionSummary,
  selectQueryResultAll,
  selectQueryResultColumn,
  selectQueryResultRow,
  selectionFocusCell,
  type QueryResultFindMatch,
  type QueryResultSelectionAnchor,
  type QueryResultSelectionRange,
} from '../utils/queryResultSelection'
import {
  fitQueryColumnsToContent,
  fitQueryColumnsToViewport,
  QUERY_RESULT_ROWNUM_WIDTH,
} from '../utils/queryColumnFitWidth'
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

type CtxMenu = {
  x: number
  y: number
  cellText: string
  tsvText: string
  rowText: string
  rowWithHeaderText: string
  selectionText?: string
  selectionLabel?: string
}

export type QueryResultServerChange = {
  filters: Record<string, FilterValue | null>
  sort: { column: string; direction: 'asc' | 'desc' } | null
}

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
  serverQuery?: boolean
  serverSort?: { column: string; direction: 'asc' | 'desc' } | null
  onServerChange?: (change: QueryResultServerChange) => void
  /** 当前视口轻量柱/折线预览（非全量 BI） */
  enableQuickChart?: boolean
  chartFields?: QueryChartField[]
  /** 行号起始偏移（结果翻页时用，0-based） */
  rowNumberOffset?: number
  /** 批量更新列宽（适合窗口）；不传则隐藏该操作 */
  onColumnWidthsChange?: (widths: Record<string, number>) => void
}

/**
 * GIDO 查询结果面板（DataGrip / DBeaver 风格）：
 * - 左侧行号，点击展开底部 KV 详情
 * - 单滚动视口 + sticky 表头；列宽拖拽，双击分隔条按内容自适应，可「适合窗口」
 * - 矩形框选；方向键 / ⌘跳边界 / Shift 扩展；⌘F 页内查找；选区聚合；适合窗口
 */
export default function QueryResultPanel({
  columns,
  dataSource,
  toolbar,
  empty,
  pagination,
  serverQuery = false,
  serverSort,
  onServerChange,
  enableQuickChart = false,
  chartFields,
  rowNumberOffset = 0,
  onColumnWidthsChange,
}: Props) {
  /** 当前展开行的 _key；null = 未选中，不显示 KV 面板 */
  const [kvKey, setKvKey] = useState<number | null>(null)
  const [kvHeight, setKvHeight] = useState<number>(240)
  const [chartOpen, setChartOpen] = useState(false)
  const [selection, setSelection] = useState<QueryResultSelectionRange | null>(null)
  const [focusCell, setFocusCell] = useState<QueryResultSelectionAnchor | null>(null)
  const [findOpen, setFindOpen] = useState(false)
  const [findQuery, setFindQuery] = useState('')
  const [findIndex, setFindIndex] = useState(0)
  const selectionAnchorRef = useRef<QueryResultSelectionAnchor | null>(null)
  const selectingRef = useRef(false)
  const gridActiveRef = useRef(false)
  const findInputRef = useRef<any>(null)
  const mainRef = useRef<HTMLDivElement>(null)
  const kvHeightClamp = useMemo(() => ({ min: 120, max: 420 }), [])
  const resizingKvRef = useRef(false)

  useEffect(() => {
    setKvKey(null)
    setSelection(null)
    setFocusCell(null)
    setFindIndex(0)
  }, [dataSource])

  const startKvResize = useCallback(
    (e: any) => {
      if (e.button !== 0) return
      e.preventDefault()
      e.stopPropagation()
      const startY = e.clientY
      const startH = kvHeight
      resizingKvRef.current = true
      document.body.style.cursor = 'row-resize'
      document.body.style.userSelect = 'none'

      const onMove = (ev: MouseEvent) => {
        if (!resizingKvRef.current) return
        const dy = ev.clientY - startY
        // Bottom panel: drag the top divider down → panel shorter (same as ResizableVerticalSplit).
        const next = Math.max(kvHeightClamp.min, Math.min(kvHeightClamp.max, startH - dy))
        setKvHeight(next)
      }

      const onUp = () => {
        resizingKvRef.current = false
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
      }

      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [kvHeight, kvHeightClamp],
  )

  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [ctx, setCtx] = useState<CtxMenu | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(pagination === false ? 100 : (pagination?.pageSize ?? 100))
  const [sort, setSort] = useState<{ field: string; order: QuerySortOrder } | null>(null)

  const ascendCacheRef = useRef<{ field: string; rows: QueryRowRec[]; fp: string } | null>(null)
  const dataFingerprint = useMemo(() => queryResultDataFingerprint(dataSource), [dataSource])

  useEffect(() => {
    if (pagination === false) return
    const next = pagination?.pageSize ?? 100
    setPageSize(previous => (previous === next ? previous : next))
    setPage(1)
  }, [pagination === false ? null : (pagination?.pageSize ?? 100)])

  useEffect(() => {
    setPage(1)
    setSort(null)
    ascendCacheRef.current = null
  }, [dataFingerprint])

  useEffect(() => {
    setSelection(null)
    setFocusCell(null)
    setFindIndex(0)
  }, [page, pageSize])

  const pagingEnabled = pagination !== false
  const pageSizeOptions = pagination === false
    ? []
    : (pagination?.pageSizeOptions ?? ['50', '100', '200', '500'])

  const sortedData = useMemo(() => {
    if (serverQuery || !sort?.field || !sort.order) return dataSource
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
  }, [dataSource, dataFingerprint, serverQuery, sort])

  const pagedData = useMemo(() => {
    if (!pagingEnabled) return sortedData
    const start = (page - 1) * pageSize
    return sortedData.slice(start, start + pageSize)
  }, [sortedData, pagingEnabled, page, pageSize])

  const resolvedChartFields = useMemo<QueryChartField[]>(() => {
    if (chartFields?.length) return chartFields
    return columns
      .map(col => {
        const name = String((col as ColumnType<QueryRowRec>).dataIndex
          ?? (col as ColumnType<QueryRowRec>).key
          ?? '')
        return name && name !== '#' ? { name } : null
      })
      .filter((item): item is QueryChartField => Boolean(item))
  }, [chartFields, columns])

  const hasPinnedDataColumns = useMemo(
    () => columns.some(col => (col as ColumnType<QueryRowRec>).fixed === 'left'),
    [columns],
  )

  const tableMinWidth = useMemo(() => {
    let w = 40 + QUERY_RESULT_ROWNUM_WIDTH
    for (const c of columns) {
      w += typeof c.width === 'number' ? c.width : 148
    }
    return w
  }, [columns])

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

  const leafKeys = useMemo(
    () => columns
      .filter(c => !('children' in (c as any)))
      .map(c => String((c as any).dataIndex ?? (c as any).key ?? ''))
      .filter(Boolean)
      .filter(key => key !== '__rownum__' && key !== '#'),
    [columns],
  )

  // Server/page caps already limit DOM rows (~200). Do not turn on antd Table
  // `virtual` — it relocates scrolling to rc-virtual-list and breaks the shared
  // H/V bars on `.dw-query-result__main`.

  const focusColumnStats = useMemo(() => {
    if (!focusCell || !leafKeys[focusCell.col]) return null
    return computeQueryColumnPageStats(pagedData, leafKeys[focusCell.col])
  }, [focusCell, leafKeys, pagedData])

  const copySelection = useCallback(async (includeHeader = false) => {
    if (!selection) return false
    const text = formatQueryResultSelectionTsv(pagedData, leafKeys, selection, { includeHeader })
    const ok = await copyToClipboard(text)
    if (ok) {
      message.success(includeHeader
        ? `已复制选区（含表头，${queryResultSelectionSummary(selection)}）`
        : `已复制选区（${queryResultSelectionSummary(selection)}）`)
    } else {
      message.error('复制失败，请手动选择后 ⌘C')
    }
    return ok
  }, [leafKeys, pagedData, selection])

  const selectionAggregates = useMemo(
    () => (selection
      ? computeQueryResultSelectionAggregates(pagedData, leafKeys, selection)
      : null),
    [leafKeys, pagedData, selection],
  )

  const findMatches = useMemo(
    () => (findOpen ? findQueryResultMatches(pagedData, leafKeys, findQuery) : []),
    [findOpen, findQuery, leafKeys, pagedData],
  )

  const findHitKeySet = useMemo(() => {
    const set = new Set<string>()
    for (const match of findMatches) set.add(`${match.row}:${match.col}`)
    return set
  }, [findMatches])

  const currentFindMatch: QueryResultFindMatch | null = findMatches.length
    ? findMatches[Math.min(Math.max(findIndex, 0), findMatches.length - 1)]
    : null

  useEffect(() => {
    if (!findMatches.length) {
      if (findIndex !== 0) setFindIndex(0)
      return
    }
    if (findIndex >= findMatches.length) setFindIndex(0)
  }, [findIndex, findMatches.length])

  const scrollCellIntoView = useCallback((cell: QueryResultSelectionAnchor) => {
    const root = mainRef.current
    if (!root) return
    const el = root.querySelector(
      `[data-qr-row="${cell.row}"][data-qr-col="${cell.col}"]`,
    ) as HTMLElement | null
    el?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [])

  const applyFocusSelection = useCallback((
    nextFocus: QueryResultSelectionAnchor,
    extend: boolean,
  ) => {
    const anchor = extend && selectionAnchorRef.current
      ? selectionAnchorRef.current
      : nextFocus
    if (!extend) selectionAnchorRef.current = nextFocus
    setFocusCell(nextFocus)
    setSelection(normalizeQueryResultSelection(anchor, nextFocus))
    window.requestAnimationFrame(() => scrollCellIntoView(nextFocus))
  }, [scrollCellIntoView])

  const selectEntireRow = useCallback((row: number) => {
    const range = selectQueryResultRow(row, leafKeys.length)
    if (!range) return
    const focus = { row, col: focusCell?.col ?? 0 }
    selectionAnchorRef.current = { row, col: 0 }
    setFocusCell(focus)
    setSelection(range)
    gridActiveRef.current = true
    window.requestAnimationFrame(() => scrollCellIntoView(focus))
  }, [focusCell?.col, leafKeys.length, scrollCellIntoView])

  const selectEntireColumn = useCallback((col: number) => {
    const range = selectQueryResultColumn(col, pagedData.length)
    if (!range) return
    const focus = { row: focusCell?.row ?? 0, col }
    selectionAnchorRef.current = { row: 0, col }
    setFocusCell(focus)
    setSelection(range)
    gridActiveRef.current = true
    window.requestAnimationFrame(() => scrollCellIntoView(focus))
  }, [focusCell?.row, pagedData.length, scrollCellIntoView])

  const selectEntirePage = useCallback(() => {
    const range = selectQueryResultAll(pagedData.length, leafKeys.length)
    if (!range) return
    const focus = focusCell ?? { row: 0, col: 0 }
    selectionAnchorRef.current = { row: 0, col: 0 }
    setFocusCell(focus)
    setSelection(range)
    gridActiveRef.current = true
  }, [focusCell, leafKeys.length, pagedData.length])

  const goToFindMatch = useCallback((index: number) => {
    if (!findMatches.length) return
    const next = ((index % findMatches.length) + findMatches.length) % findMatches.length
    setFindIndex(next)
    const match = findMatches[next]
    selectionAnchorRef.current = match
    setFocusCell(match)
    setSelection(normalizeQueryResultSelection(match, match))
    window.requestAnimationFrame(() => scrollCellIntoView(match))
  }, [findMatches, scrollCellIntoView])

  const openFind = useCallback(() => {
    setFindOpen(true)
    gridActiveRef.current = true
    window.setTimeout(() => findInputRef.current?.focus?.({ cursor: 'all' }), 0)
  }, [])

  const applyColumnFit = useCallback((mode: 'content' | 'viewport') => {
    if (!onColumnWidthsChange || !leafKeys.length) return
    const viewportWidth = mainRef.current?.clientWidth || 800
    const widths = mode === 'content'
      ? fitQueryColumnsToContent({ columns: leafKeys, rows: pagedData })
      : fitQueryColumnsToViewport({
        columns: leafKeys,
        rows: pagedData,
        viewportWidth,
        reservedWidth: QUERY_RESULT_ROWNUM_WIDTH,
      })
    onColumnWidthsChange(widths)
    message.success(mode === 'content'
      ? '已按内容展开列宽（表头可读，可横向滚动）'
      : '已调整列宽：按内容展开，有空余则填满窗口')
  }, [leafKeys, onColumnWidthsChange, pagedData])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target instanceof Element ? event.target : null
      const inFindInput = Boolean(target?.closest('.dw-query-result__find'))
      const inEditable = Boolean(target?.closest('input, textarea, [contenteditable="true"]'))
      const meta = event.metaKey || event.ctrlKey

      if (meta && (event.key === 'f' || event.key === 'F')) {
        if (!gridActiveRef.current && !(target && mainRef.current?.contains(target))) return
        event.preventDefault()
        openFind()
        return
      }

      if (event.key === 'Escape') {
        if (findOpen) {
          setFindOpen(false)
          return
        }
        setSelection(null)
        setFocusCell(null)
        selectionAnchorRef.current = null
        return
      }

      if (findOpen && findMatches.length && (event.key === 'F3' || (event.key === 'Enter' && inFindInput))) {
        event.preventDefault()
        goToFindMatch(findIndex + (event.shiftKey ? -1 : 1))
        return
      }

      if (inEditable && !inFindInput) return

      if (meta && (event.key === 'c' || event.key === 'C')) {
        if (!selection) return
        event.preventDefault()
        void copySelection(event.shiftKey)
        return
      }

      if (inFindInput) return

      if (meta && (event.key === 'a' || event.key === 'A')) {
        if (!pagedData.length || !leafKeys.length) return
        if (!gridActiveRef.current && !(target && mainRef.current?.contains(target))) return
        event.preventDefault()
        selectEntirePage()
        return
      }

      // Ctrl/Cmd+Space：选中焦点所在列（整页）
      if (meta && event.code === 'Space') {
        if (!pagedData.length || !leafKeys.length) return
        const col = focusCell?.col ?? selection?.startCol
        if (col == null || col < 0) return
        event.preventDefault()
        selectEntireColumn(col)
        return
      }

      const navKeys = new Set([
        'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
        'Home', 'End', 'PageUp', 'PageDown',
      ])
      if (!navKeys.has(event.key)) return
      if (!pagedData.length || !leafKeys.length) return

      event.preventDefault()
      const bounds = { rows: pagedData.length, cols: leafKeys.length }
      const anchor = selectionAnchorRef.current
      const focus = focusCell
        ?? (selection ? selectionFocusCell(selection, anchor) : { row: 0, col: 0 })
      const nextFocus = moveQueryResultFocus(focus, event.key, bounds, { jump: meta })
      if (!nextFocus) return
      applyFocusSelection(nextFocus, event.shiftKey && Boolean(anchor || selection || focusCell))
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [
    applyFocusSelection,
    copySelection,
    findIndex,
    findMatches.length,
    findOpen,
    focusCell,
    goToFindMatch,
    leafKeys.length,
    openFind,
    pagedData.length,
    selectEntireColumn,
    selectEntirePage,
    selection,
  ])

  useEffect(() => {
    const endSelect = () => {
      selectingRef.current = false
    }
    window.addEventListener('mouseup', endSelect)
    return () => window.removeEventListener('mouseup', endSelect)
  }, [])

  useEffect(() => {
    if (!currentFindMatch) return
    window.requestAnimationFrame(() => scrollCellIntoView(currentFindMatch))
  }, [currentFindMatch, scrollCellIntoView])

  const columnsWithCopy = useMemo((): ColumnsType<QueryRowRec> => {
    return columns.map(col => {
      if ('children' in col && col.children) return col
      const leaf = col as ColumnType<QueryRowRec>
      const field = String(leaf.dataIndex ?? leaf.key ?? '')
      const colIndex = leafKeys.indexOf(field)
      const origRender = leaf.render
      return {
        ...leaf,
        sorter: field ? { compare: () => 0 } : undefined,
        sortOrder: serverQuery
          ? (serverSort?.column === field ? (serverSort.direction === 'asc' ? 'ascend' : 'descend') : null)
          : (sort && field && sort.field === field ? sort.order : null),
        sortDirections: ['ascend', 'descend'] as const,
        showSorterTooltip: { title: '点击升序 · 再点降序 · 再点取消' },
        onCell: (record, index) => {
          const rowIndex = typeof index === 'number' ? index : pagedData.indexOf(record)
          const classes = [
            colIndex >= 0 && isQueryResultCellSelected(selection, rowIndex, colIndex)
              ? 'dw-query-result__cell--selected'
              : '',
            focusCell && focusCell.row === rowIndex && focusCell.col === colIndex
              ? 'dw-query-result__cell--focus'
              : '',
            colIndex >= 0 && findHitKeySet.has(`${rowIndex}:${colIndex}`)
              ? 'dw-query-result__cell--find-hit'
              : '',
            currentFindMatch
              && currentFindMatch.row === rowIndex
              && currentFindMatch.col === colIndex
              ? 'dw-query-result__cell--find-current'
              : '',
          ].filter(Boolean)
          return classes.length ? { className: classes.join(' ') } : {}
        },
        onHeaderCell: () => ({
          title: '⌘/Ctrl+单击表头选中整列',
          onMouseDown: (event: ReactMouseEvent) => {
            if (!(event.metaKey || event.ctrlKey) || colIndex < 0) return
            event.preventDefault()
            event.stopPropagation()
            selectEntireColumn(colIndex)
          },
        }),
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
          } else if (value === null || value === undefined) {
            inner = <span style={{ color: '#bfbfbf' }}>NULL</span>
          } else {
            inner = <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>
          }
          return (
            <div
              className={`dw-cell-value${copiedKey === cellKey ? ' dw-cell-value--copied' : ''}`}
              data-qr-row={index}
              data-qr-col={colIndex}
              title="方向键移动；⌘/Ctrl+方向键跳边界；Shift 扩展；⌘/Ctrl+A 全选；⌘/Ctrl+Space 选列；⌘/Ctrl+F 查找；⌘/Ctrl+C 复制"
              tabIndex={0}
              role="gridcell"
              aria-selected={colIndex >= 0 && isQueryResultCellSelected(selection, index, colIndex)}
              aria-label={`${field} 单元格`}
              onMouseDown={e => {
                if (e.button !== 0 || colIndex < 0) return
                if ((e.target as HTMLElement).closest('button, a, input, textarea')) return
                e.preventDefault()
                gridActiveRef.current = true
                selectingRef.current = true
                applyFocusSelection({ row: index, col: colIndex }, e.shiftKey)
              }}
              onMouseEnter={() => {
                if (!selectingRef.current || colIndex < 0 || !selectionAnchorRef.current) return
                const next = { row: index, col: colIndex }
                setFocusCell(next)
                setSelection(normalizeQueryResultSelection(selectionAnchorRef.current, next))
              }}
              onKeyDown={e => {
                if (e.key === 'Escape') {
                  setSelection(null)
                  setFocusCell(null)
                  return
                }
                if (e.key !== 'Enter' && e.key !== ' ') return
                e.preventDefault()
                void doCopy(text, '已复制单元格')
              }}
              onDoubleClick={e => {
                e.stopPropagation()
                void doCopy(text, '已复制单元格')
                setCopiedKey(cellKey)
                window.setTimeout(() => setCopiedKey(k => (k === cellKey ? null : k)), 600)
              }}
              onContextMenu={e => {
                e.preventDefault()
                e.stopPropagation()
                const rowText = leafKeys.map(key => formatQueryCellValue(record[key])).join('\t')
                const tsv = `${field}\t${text}`
                const selectionText = selection
                  ? formatQueryResultSelectionTsv(pagedData, leafKeys, selection)
                  : ''
                setCtx({
                  x: e.clientX,
                  y: e.clientY,
                  cellText: text,
                  tsvText: tsv,
                  rowText,
                  rowWithHeaderText: `${leafKeys.join('\t')}\n${rowText}`,
                  selectionText,
                  selectionLabel: selection ? queryResultSelectionSummary(selection) : undefined,
                })
              }}
            >
              {inner}
            </div>
          )
        },
      }
    })
  }, [
    applyFocusSelection,
    columns,
    copiedKey,
    currentFindMatch,
    doCopy,
    findHitKeySet,
    focusCell,
    leafKeys,
    pagedData,
    selectEntireColumn,
    selection,
    serverQuery,
    serverSort,
    sort,
  ])

  /** 行号列：单击选中整行；双击展开 KV；⌘/Ctrl+单击仅展开详情 */
  const rowNumColumn: ColumnType<QueryRowRec> = useMemo(() => ({
    key: '__rownum__',
    dataIndex: '__rownum__',
    title: (
      <span
        className="dw-rownum-header"
        title="单击行号选中整行；双击展开行详情"
        onClick={e => {
          e.stopPropagation()
          selectEntirePage()
        }}
      >
        <TableOutlined style={{ fontSize: 11 }} />
      </span>
    ),
    width: QUERY_RESULT_ROWNUM_WIDTH,
    fixed: 'left' as const,
    render: (_: unknown, record: QueryRowRec, index: number) => {
      const k = (record as any)._key as number
      const active = kvKey === k
      const rowSelected = Boolean(
        selection
        && selection.startRow === index
        && selection.endRow === index
        && selection.startCol === 0
        && selection.endCol === Math.max(0, leafKeys.length - 1),
      )
      const displayNum = rowNumberOffset + (page - 1) * pageSize + index + 1
      return (
        <button
          type="button"
          className={`dw-rownum-btn${active || rowSelected ? ' dw-rownum-btn--active' : ''}`}
          title={active ? '双击收起详情 · 单击选中整行' : '单击选中整行 · 双击展开详情'}
          onClick={e => {
            e.stopPropagation()
            if (e.metaKey || e.ctrlKey) {
              setKvKey(prev => (prev === k ? null : k))
              return
            }
            selectEntireRow(index)
          }}
          onDoubleClick={e => {
            e.stopPropagation()
            setKvKey(prev => (prev === k ? null : k))
          }}
        >
          {displayNum}
        </button>
      )
    },
  }), [kvKey, leafKeys.length, page, pageSize, rowNumberOffset, selectEntirePage, selectEntireRow, selection])

  const allColumns = useMemo(
    () => [rowNumColumn, ...columnsWithCopy],
    [rowNumColumn, columnsWithCopy],
  )

  const onTableChange: TableProps<QueryRowRec>['onChange'] = useCallback((_pag, filters, sorter) => {
    if (shouldSuppressHeaderInteraction()) return
    const s = (Array.isArray(sorter) ? sorter[0] : sorter) as SorterResult<QueryRowRec>
    const field = s?.field != null ? String(s.field) : (s?.columnKey != null ? String(s.columnKey) : '')
    const order = s?.order
    if (serverQuery) {
      onServerChange?.({
        filters,
        sort: field && (order === 'ascend' || order === 'descend')
          ? { column: field, direction: order === 'ascend' ? 'asc' : 'desc' }
          : null,
      })
      return
    }
    startTransition(() => {
      if (field && (order === 'ascend' || order === 'descend')) {
        setSort({ field, order })
        setPage(1)
      } else {
        setSort(null)
      }
    })
  }, [onServerChange, serverQuery])

  /** 当前选中行的数据 */
  const kvRowData = useMemo(
    () => kvKey != null ? dataSource.find(r => (r as any)._key === kvKey) ?? null : null,
    [dataSource, kvKey],
  )

  if (!dataSource.length && empty) {
    return <div className="dw-query-result">{empty}</div>
  }

  return (
    <div className="dw-query-result">
      <div className="dw-query-result__toolbar">
        <div className="dw-query-result__toolbar-start">
          {toolbar}
        </div>
        <div className="dw-query-result__toolbar-end">
          <Tooltip title="在当前页结果中查找（⌘/Ctrl+F）">
            <Button
              size="small"
              icon={<SearchOutlined />}
              disabled={!pagedData.length || leafKeys.length < 1}
              onClick={openFind}
            >
              查找
            </Button>
          </Tooltip>
          {onColumnWidthsChange ? (
            <Dropdown.Button
              size="small"
              disabled={!pagedData.length || leafKeys.length < 1}
              onClick={() => applyColumnFit('viewport')}
              menu={{
                items: [
                  {
                    key: 'viewport',
                    label: '适合窗口',
                    title: '按内容展开；总宽小于窗口时再拉伸填满，绝不压窄表头',
                  },
                  {
                    key: 'content',
                    label: '展开表头',
                    title: '仅按字段名与内容展开，便于看清全部列名',
                  },
                ],
                onClick: ({ key }) => applyColumnFit(key === 'content' ? 'content' : 'viewport'),
              }}
            >
              <ColumnWidthOutlined /> 适合窗口
            </Dropdown.Button>
          ) : null}
          {enableQuickChart ? (
            <Tooltip title="基于当前视口已显示的行画轻量柱/折线，非全量分析">
              <Button
                size="small"
                icon={<BarChartOutlined />}
                disabled={!pagedData.length || resolvedChartFields.length < 1}
                onClick={() => setChartOpen(true)}
              >
                图表
              </Button>
            </Tooltip>
          ) : null}
        </div>
      </div>
      {findOpen ? (
        <div className="dw-query-result__find">
          <Input
            ref={findInputRef}
            size="small"
            allowClear
            placeholder="在当前页查找…"
            value={findQuery}
            onChange={event => {
              setFindQuery(event.target.value)
              setFindIndex(0)
            }}
            onPressEnter={event => {
              event.preventDefault()
              goToFindMatch(findIndex + (event.shiftKey ? -1 : 1))
            }}
            style={{ width: 220 }}
          />
          <span className="dw-query-result__find-count">
            {findQuery.trim()
              ? (findMatches.length ? `${Math.min(findIndex, findMatches.length - 1) + 1}/${findMatches.length}` : '0/0')
              : '—'}
          </span>
          <Button size="small" disabled={!findMatches.length} onClick={() => goToFindMatch(findIndex - 1)}>
            上一个
          </Button>
          <Button size="small" disabled={!findMatches.length} onClick={() => goToFindMatch(findIndex + 1)}>
            下一个
          </Button>
          <Button size="small" type="text" onClick={() => setFindOpen(false)}>
            关闭
          </Button>
        </div>
      ) : null}
      {selection && selectionAggregates ? (
        <div className="dw-query-result__aggregate" role="status">
          <span>{queryResultSelectionSummary(selection)}</span>
          <span className="dw-query-result__aggregate-sep">·</span>
          <span>非空 {selectionAggregates.cells - selectionAggregates.nulls}</span>
          <span className="dw-query-result__aggregate-sep">·</span>
          <span>NULL {selectionAggregates.nulls}</span>
          {selectionAggregates.numericCount > 0 ? (
            <>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Sum {formatQueryResultAggregateNumber(selectionAggregates.sum!)}</span>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Avg {formatQueryResultAggregateNumber(selectionAggregates.avg!)}</span>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Min {formatQueryResultAggregateNumber(selectionAggregates.min!)}</span>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Max {formatQueryResultAggregateNumber(selectionAggregates.max!)}</span>
            </>
          ) : null}
          {focusColumnStats ? (
            <>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span className="dw-query-result__aggregate-col">
                列 {focusColumnStats.column}
                ：distinct {focusColumnStats.distinct}
                {focusColumnStats.numericCount > 0
                  ? ` · Σ ${formatQueryResultAggregateNumber(focusColumnStats.sum!)}`
                  : ` · null ${focusColumnStats.nulls}`}
              </span>
            </>
          ) : null}
        </div>
      ) : focusColumnStats ? (
        <div className="dw-query-result__aggregate" role="status">
          <span className="dw-query-result__aggregate-col">
            列 {focusColumnStats.column}
          </span>
          <span className="dw-query-result__aggregate-sep">·</span>
          <span>{focusColumnStats.cells} 行</span>
          <span className="dw-query-result__aggregate-sep">·</span>
          <span>NULL {focusColumnStats.nulls}</span>
          <span className="dw-query-result__aggregate-sep">·</span>
          <span>distinct {focusColumnStats.distinct}</span>
          {focusColumnStats.numericCount > 0 ? (
            <>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Sum {formatQueryResultAggregateNumber(focusColumnStats.sum!)}</span>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Min {formatQueryResultAggregateNumber(focusColumnStats.min!)}</span>
              <span className="dw-query-result__aggregate-sep">·</span>
              <span>Max {formatQueryResultAggregateNumber(focusColumnStats.max!)}</span>
            </>
          ) : null}
        </div>
      ) : null}
      {enableQuickChart ? (
        <QueryResultChartDrawer
          open={chartOpen}
          onClose={() => setChartOpen(false)}
          rows={pagedData as Array<Record<string, unknown>>}
          fields={resolvedChartFields}
        />
      ) : null}
      <div className="dw-query-result__viewport">
        <div
          ref={mainRef}
          className={[
            'dw-query-result__main',
            hasPinnedDataColumns ? 'dw-query-result__main--has-pinned' : '',
          ].filter(Boolean).join(' ')}
          title="滚轮或拖动滚动条；方向键移动；⌘/Ctrl+A 全选；⌘/Ctrl+Space 选列；⌘/Ctrl+F 查找"
          onMouseDown={() => {
            gridActiveRef.current = true
          }}
        >
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
            rowClassName={(record, index) => {
              const classes = []
              const k = (record as any)._key as number
              if (k === kvKey) classes.push('dw-row--selected')
              if (focusCell && focusCell.row === index) classes.push('dw-row--focus')
              return classes.join(' ')
            }}
            showSorterTooltip={{ title: '点击升序 · 再点降序 · 再点取消' }}
          />
        </div>
      </div>
      {/* KV 详情面板：点击行号后在底部展开，两区域同时可见（DBeaver 风格） */}
      {kvRowData && (
        <div className="dw-query-result__kv" style={{ height: kvHeight }}>
          <div
            className="dw-query-result__kv-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            onMouseDown={startKvResize}
            title="拖拽调整详情面板高度"
          />
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
            showQuickJumper
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
          {ctx.selectionText ? (
            <>
              <button type="button" onClick={() => void doCopy(ctx.selectionText!, `已复制选区（${ctx.selectionLabel || '单元格'}）`)}>
                复制选区{ctx.selectionLabel ? `（${ctx.selectionLabel}）` : ''}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!selection) return
                  void copySelection(true)
                  setCtx(null)
                }}
              >
                复制选区（含表头）
              </button>
            </>
          ) : null}
          <button type="button" onClick={() => void doCopy(ctx.tsvText, '已复制（列名 + 制表符 + 值）')}>
            复制为 TSV
          </button>
          <button type="button" onClick={() => void doCopy(ctx.rowText, '已复制整行')}>
            复制整行
          </button>
          <button type="button" onClick={() => void doCopy(ctx.rowWithHeaderText, '已复制带表头 TSV')}>
            复制整行（带表头 TSV）
          </button>
        </div>
      )}
    </div>
  )
}

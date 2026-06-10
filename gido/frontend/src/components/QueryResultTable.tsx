/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useRef, type TdHTMLAttributes, type ThHTMLAttributes, type ReactNode } from 'react'
import { message } from 'antd'
import { HolderOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import { mergeColumnOrderWithKeys } from '../utils/resultTableMeta'
import type { QueryColumnMeta } from '../utils/queryColumns'
import { classifyColumnType } from '../utils/columnTypeBadge'
import { formatCellDisplay } from '../utils/cellDisplay'
import {
  ColumnFilterDropdown,
  columnFilterPredicate,
  distinctValuesForColumn,
} from './ColumnFilterDropdown'
import './queryResultPanel.css'

const COL_DND_MIME = 'application/x-gido-col'

export type QueryRowRec = Record<string, unknown> & { _key: number }

export type ResultColumnBuildOpts = {
  order?: string[] | null
  widths?: Record<string, number> | null
  /** 当前结果行，用于列筛选去重值列表 */
  dataSource?: QueryRowRec[]
  onOrderChange?: (nextOrder: string[]) => void
  onWidthChange?: (key: string, width: number) => void
}

async function copyText(text: string): Promise<boolean> {
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

/** 表头 / 表体默认可选中文本；列顺序仅通过左侧握把拖拽，避免整块表头 draggable 导致无法复制列名 */
export const queryResultTableComponents = {
  header: {
    cell: (props: ThHTMLAttributes<HTMLTableCellElement> & { children?: ReactNode }) => {
      const { style, ...rest } = props
      return (
        <th
          {...rest}
          style={{
            ...style,
            userSelect: 'text',
            WebkitUserSelect: 'text',
          }}
        />
      )
    },
  },
  body: {
    cell: (props: TdHTMLAttributes<HTMLTableCellElement> & { children?: ReactNode }) => {
      const { style, ...rest } = props
      return (
        <td
          {...rest}
          style={{
            ...style,
            userSelect: 'text',
            WebkitUserSelect: 'text',
          }}
        />
      )
    },
  },
}

function ColumnHeaderChrome({
  col,
  typeLabel,
  width,
  canReorder,
  onReorderPair,
  onWidthChange,
}: {
  col: string
  typeLabel?: string
  width: number
  canReorder: boolean
  onReorderPair?: (from: string, to: string) => void
  onWidthChange?: (key: string, w: number) => void
}) {
  const drag = useRef<{ startX: number; startW: number } | null>(null)

  const onResizeMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (!onWidthChange) return
      e.preventDefault()
      e.stopPropagation()
      drag.current = { startX: e.clientX, startW: width }

      const onMove = (ev: MouseEvent) => {
        const d = drag.current
        if (!d) return
        // 拖列右缘：向右拉变宽、向左拉变窄（与 Excel / DataGrip 一致）
        const nw = Math.max(56, Math.min(720, d.startW + (ev.clientX - d.startX)))
        onWidthChange(col, nw)
      }
      const onUp = () => {
        drag.current = null
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [col, width, onWidthChange],
  )

  const copyColName = async (e: React.MouseEvent) => {
    e.stopPropagation()
    const ok = await copyText(col)
    if (ok) message.success('已复制列名')
    else message.error('复制失败，请拖选列名后 ⌘C')
  }

  const typeBadge = classifyColumnType(typeLabel)
  const headerTitle = typeBadge ? `${col} (${typeBadge.title})` : col

  return (
    <div
      className="dw-col-header-root"
      style={{ display: 'flex', alignItems: 'stretch', width: '100%', minHeight: 32 }}
      onDragOver={e => {
        if (!canReorder || !onReorderPair) return
        if ([...e.dataTransfer.types].includes(COL_DND_MIME)) {
          e.preventDefault()
          e.dataTransfer.dropEffect = 'move'
        }
      }}
      onDrop={e => {
        if (!canReorder || !onReorderPair) return
        const from = e.dataTransfer.getData(COL_DND_MIME)
        if (from && from !== col) onReorderPair(from, col)
      }}
    >
      <div style={{ display: 'flex', flex: 1, minWidth: 0, alignItems: 'center', gap: 4 }}>
        {canReorder && onReorderPair && (
          <span
            draggable
            className="dw-col-drag-handle"
            onDragStart={ev => {
              ev.dataTransfer.setData(COL_DND_MIME, col)
              ev.dataTransfer.effectAllowed = 'move'
            }}
            onClick={e => e.stopPropagation()}
            title="拖动调整列顺序"
          >
            <HolderOutlined />
          </span>
        )}
        <div
          className="dw-col-header-title-wrap"
          title={`${headerTitle} — 可拖选复制；双击复制列名`}
          onDoubleClick={copyColName}
          onContextMenu={e => {
            const sel = typeof window !== 'undefined' ? window.getSelection()?.toString()?.trim() : ''
            if (sel) return
            e.preventDefault()
            e.stopPropagation()
            void copyColName(e)
          }}
        >
          <span className="dw-col-header-title" style={{ userSelect: 'text', WebkitUserSelect: 'text', cursor: 'text' }}>
            {col}
          </span>
          {typeBadge ? (
            <span className={`dw-col-type-badge dw-col-type-badge--${typeBadge.kind}`} aria-hidden>
              {typeBadge.badge}
            </span>
          ) : null}
        </div>
      </div>
      {onWidthChange && (
        <div
          role="separator"
          aria-orientation="vertical"
          className="dw-col-resize-handle"
          onMouseDown={onResizeMouseDown}
          title="拖拽调整列宽（固定在列右侧，类似滚动条）"
        />
      )}
    </div>
  )
}

export function buildQueryTableColumns(
  columns: string[] | QueryColumnMeta[],
  opts?: ResultColumnBuildOpts,
): ColumnsType<QueryRowRec> {
  const metas: QueryColumnMeta[] =
    columns.length > 0 && typeof columns[0] === 'object' && columns[0] !== null && 'name' in (columns[0] as object)
      ? (columns as QueryColumnMeta[])
      : (columns as string[]).map(name => ({ name }))
  const names = metas.map(m => m.name)
  const typeByName = Object.fromEntries(metas.map(m => [m.name, m.type]))
  const keys = mergeColumnOrderWithKeys(opts?.order ?? null, names)
  const canReorder = Boolean(opts?.onOrderChange)
  const onWidthChange = opts?.onWidthChange

  const onReorderPair =
    opts?.onOrderChange &&
    ((from: string, to: string) => {
      const order = [...keys]
      const i = order.indexOf(from)
      const j = order.indexOf(to)
      if (i < 0 || j < 0 || i === j) return
      order.splice(i, 1)
      order.splice(j, 0, from)
      opts.onOrderChange!(order)
    })

  const rowData = opts?.dataSource ?? []

  return keys.map(col => {
    const w = opts?.widths?.[col] ?? 148
    const distinctValues = distinctValuesForColumn(rowData, col)
    return {
      title: (
        <ColumnHeaderChrome
          key={col}
          col={col}
          typeLabel={typeByName[col]}
          width={w}
          canReorder={canReorder}
          onReorderPair={onReorderPair || undefined}
          onWidthChange={onWidthChange}
        />
      ),
      dataIndex: col,
      key: col,
      ellipsis: true,
      width: w,
      filterMultiple: true,
      filterDropdown: props => (
        <ColumnFilterDropdown col={col} distinctValues={distinctValues} {...props} />
      ),
      onFilter: (value, record) => columnFilterPredicate(col, String(value), record),
      render: (v: unknown) => {
        if (v === null || v === 'None') {
          return <span style={{ color: '#bfbfbf' }}>NULL</span>
        }
        const text = formatCellDisplay(v)
        return (
          <span style={{ fontFamily: 'monospace', fontSize: 12 }} title={text.length > 80 ? text : undefined}>
            {text}
          </span>
        )
      },
    }
  })
}

export function rowsToRecordDataSource(columns: string[], rows: unknown[][]): QueryRowRec[] {
  return rows.map((row, i) => {
    const obj: QueryRowRec = { _key: i }
    columns.forEach((c, ci) => {
      obj[c] = row[ci] ?? ''
    })
    return obj
  })
}

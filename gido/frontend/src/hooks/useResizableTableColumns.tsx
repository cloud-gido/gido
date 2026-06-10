/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useMemo, useState } from 'react'
import type { ColumnsType, ColumnType } from 'antd/es/table'
import ResizableColumnTitle from '../components/ResizableColumnTitle'

type ColWithKey<T> = ColumnType<T> & { _resizeKey?: string }

function colKey<T>(col: ColumnType<T>, index: number): string {
  const c = col as ColWithKey<T>
  if (c._resizeKey) return c._resizeKey
  if (col.key != null) return String(col.key)
  if (col.dataIndex != null) return String(col.dataIndex)
  return `col_${index}`
}

function loadWidths(storageKey: string | undefined, defaults: Record<string, number>): Record<string, number> {
  if (!storageKey) return { ...defaults }
  try {
    const raw = localStorage.getItem(storageKey)
    if (!raw) return { ...defaults }
    const parsed = JSON.parse(raw) as Record<string, number>
    return { ...defaults, ...parsed }
  } catch {
    return { ...defaults }
  }
}

/**
 * 为 Ant Design Table 列增加可拖拽列宽（表头单行、右侧分隔条）。
 */
export function useResizableTableColumns<T>(
  baseColumns: ColumnsType<T>,
  options?: {
    storageKey?: string
    defaultWidths?: Record<string, number>
  },
): ColumnsType<T> {
  const defaults = useMemo(() => {
    const d: Record<string, number> = { ...(options?.defaultWidths || {}) }
    baseColumns.forEach((col, i) => {
      const k = colKey(col, i)
      if (d[k] == null && col.width != null) d[k] = Number(col.width)
    })
    return d
  }, [baseColumns, options?.defaultWidths])

  const [widths, setWidths] = useState<Record<string, number>>(() =>
    loadWidths(options?.storageKey, defaults),
  )

  const setWidth = useCallback(
    (key: string, w: number) => {
      setWidths(prev => {
        const next = { ...prev, [key]: w }
        if (options?.storageKey) {
          try {
            localStorage.setItem(options.storageKey, JSON.stringify(next))
          } catch {
            /* ignore */
          }
        }
        return next
      })
    },
    [options?.storageKey],
  )

  return useMemo(
    () =>
      baseColumns.map((col, index) => {
        const key = colKey(col, index)
        const w = widths[key] ?? defaults[key] ?? 120
        const titleText = typeof col.title === 'string' ? col.title : key
        const resizable = col.width != null || defaults[key] != null || options?.defaultWidths?.[key] != null
        if (!resizable || typeof col.title !== 'string') {
          return { ...col, width: col.width ?? w }
        }
        return {
          ...col,
          width: w,
          title: (
            <ResizableColumnTitle
              title={titleText}
              width={w}
              minWidth={64}
              maxWidth={520}
              onResize={nw => setWidth(key, nw)}
            />
          ),
        }
      }),
    [baseColumns, widths, defaults, setWidth, options?.defaultWidths],
  )
}

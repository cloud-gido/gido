/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useMemo, useState, type Key } from 'react'
import { Input, Button, Space, Checkbox } from 'antd'
import { formatCellDisplay } from '../utils/cellDisplay'

const CONTAINS_PREFIX = '__contains:'

export function distinctValuesForColumn(
  data: Record<string, unknown>[],
  col: string,
  limit = 80,
): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const row of data) {
    const s = formatCellDisplay(row[col], 0)
    if (seen.has(s)) continue
    seen.add(s)
    out.push(s)
    if (out.length >= limit) break
  }
  return out.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
}

export function columnFilterPredicate(col: string, filterKey: string | number | boolean, record: Record<string, unknown>) {
  const key = String(filterKey)
  const text = formatCellDisplay(record[col], 0)
  if (key.startsWith(CONTAINS_PREFIX)) {
    const q = key.slice(CONTAINS_PREFIX.length).toLowerCase()
    return text.toLowerCase().includes(q)
  }
  return text === key
}

type Props = {
  col: string
  distinctValues: string[]
  setSelectedKeys: (keys: Key[]) => void
  selectedKeys: Key[]
  confirm: () => void
  clearFilters?: () => void
}

export function ColumnFilterDropdown({
  distinctValues,
  setSelectedKeys,
  selectedKeys,
  confirm,
  clearFilters,
}: Props) {
  const [listSearch, setListSearch] = useState('')
  const [containsText, setContainsText] = useState('')

  const keys = (selectedKeys as string[]) || []
  const checkedValues = keys.filter(k => !String(k).startsWith(CONTAINS_PREFIX))
  const containsKey = keys.find(k => String(k).startsWith(CONTAINS_PREFIX))
  const activeContains = containsKey ? String(containsKey).slice(CONTAINS_PREFIX.length) : ''

  const visibleValues = useMemo(() => {
    const q = listSearch.trim().toLowerCase()
    if (!q) return distinctValues
    return distinctValues.filter(v => {
      const label = v === '' ? '(空)' : v
      return label.toLowerCase().includes(q)
    })
  }, [distinctValues, listSearch])

  const applyContains = (text: string) => {
    const t = text.trim()
    const next = checkedValues.slice()
    if (t) next.push(`${CONTAINS_PREFIX}${t}`)
    setSelectedKeys(next)
  }

  return (
    <div className="dw-col-filter-dropdown" onKeyDown={e => e.stopPropagation()}>
      {distinctValues.length > 0 ? (
        <>
          <Input
            size="small"
            allowClear
            placeholder="搜索可选值"
            value={listSearch}
            onChange={e => setListSearch(e.target.value)}
            style={{ marginBottom: 6 }}
          />
          <div className="dw-col-filter-values">
            <Checkbox.Group
              value={checkedValues}
              onChange={vals => {
                const next = [...(vals as string[])]
                if (activeContains) next.push(`${CONTAINS_PREFIX}${activeContains}`)
                setSelectedKeys(next)
              }}
              style={{ display: 'flex', flexDirection: 'column', gap: 2, width: '100%' }}
            >
              {visibleValues.map(v => (
                <Checkbox key={v || '__empty__'} value={v} style={{ marginInlineStart: 0 }}>
                  <span className="dw-col-filter-value-label" title={v === '' ? '(空)' : v}>
                    {v === '' ? '(空)' : v}
                  </span>
                </Checkbox>
              ))}
            </Checkbox.Group>
            {visibleValues.length === 0 ? (
              <div className="dw-col-filter-empty">无匹配项</div>
            ) : null}
          </div>
          <div className="dw-col-filter-actions-inline">
            <button
              type="button"
              className="dw-col-filter-link"
              onClick={() => {
                const next = [...distinctValues]
                if (activeContains) next.push(`${CONTAINS_PREFIX}${activeContains}`)
                setSelectedKeys(next)
              }}
            >
              全选
            </button>
            <button
              type="button"
              className="dw-col-filter-link"
              onClick={() => setSelectedKeys(activeContains ? [`${CONTAINS_PREFIX}${activeContains}`] : [])}
            >
              清空
            </button>
          </div>
        </>
      ) : null}
      <Input
        size="small"
        placeholder="包含即显示"
        value={containsText || activeContains}
        onChange={e => setContainsText(e.target.value)}
        onPressEnter={() => {
          applyContains(containsText || activeContains)
          confirm()
        }}
        style={{ marginTop: distinctValues.length > 0 ? 8 : 0, marginBottom: 8 }}
      />
      <Space>
        <Button
          type="primary"
          size="small"
          onClick={() => {
            if (containsText.trim() || activeContains) applyContains(containsText || activeContains)
            confirm()
          }}
        >
          筛选
        </Button>
        <Button
          size="small"
          onClick={() => {
            setListSearch('')
            setContainsText('')
            clearFilters?.()
            confirm()
          }}
        >
          重置
        </Button>
      </Space>
    </div>
  )
}

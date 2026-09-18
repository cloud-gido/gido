/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useMemo, useState, type Key } from 'react'
import { Input, Button, Space, Checkbox, Spin, Tag } from 'antd'
import { formatCellDisplay } from '../utils/cellDisplay'

const CONTAINS_PREFIX = '__contains:'
const STARTS_WITH_PREFIX = '__starts_with:'
const GTE_PREFIX = '__gte:'
const LTE_PREFIX = '__lte:'
export const NULL_FILTER_KEY = '__gido_null__'

export function valueToFilterKey(value: unknown): string {
  if (value === null || value === undefined) return NULL_FILTER_KEY
  return formatCellDisplay(value, 0)
}

export function distinctValuesForColumn(
  data: Record<string, unknown>[],
  col: string,
  limit = 80,
): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const row of data) {
    const s = valueToFilterKey(row[col])
    if (seen.has(s)) continue
    seen.add(s)
    out.push(s)
    if (out.length >= limit) break
  }
  return out.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
}

function isOperatorKey(key: string): boolean {
  return (
    key.startsWith(CONTAINS_PREFIX)
    || key.startsWith(STARTS_WITH_PREFIX)
    || key.startsWith(GTE_PREFIX)
    || key.startsWith(LTE_PREFIX)
  )
}

export function columnFilterPredicate(col: string, filterKey: string | number | boolean, record: Record<string, unknown>) {
  const key = String(filterKey)
  if (key === NULL_FILTER_KEY) return record[col] === null || record[col] === undefined
  const text = formatCellDisplay(record[col], 0)
  if (key.startsWith(CONTAINS_PREFIX)) {
    const q = key.slice(CONTAINS_PREFIX.length).toLowerCase()
    return text.toLowerCase().includes(q)
  }
  if (key.startsWith(STARTS_WITH_PREFIX)) {
    const q = key.slice(STARTS_WITH_PREFIX.length).toLowerCase()
    return text.toLowerCase().startsWith(q)
  }
  if (key.startsWith(GTE_PREFIX)) {
    const bound = Number(key.slice(GTE_PREFIX.length))
    const actual = Number(record[col])
    return Number.isFinite(actual) && Number.isFinite(bound) && actual >= bound
  }
  if (key.startsWith(LTE_PREFIX)) {
    const bound = Number(key.slice(LTE_PREFIX.length))
    const actual = Number(record[col])
    return Number.isFinite(actual) && Number.isFinite(bound) && actual <= bound
  }
  return text === key
}

type Props = {
  col: string
  distinctValues: string[]
  semanticType?: string
  loadDistinctValues?: () => Promise<{ values: string[]; truncated?: boolean }>
  setSelectedKeys: (keys: Key[]) => void
  selectedKeys: Key[]
  confirm: () => void
  clearFilters?: () => void
}

export function ColumnFilterDropdown({
  distinctValues,
  semanticType,
  loadDistinctValues,
  setSelectedKeys,
  selectedKeys,
  confirm,
  clearFilters,
}: Props) {
  const [listSearch, setListSearch] = useState('')
  const [containsText, setContainsText] = useState('')
  const [startsWithText, setStartsWithText] = useState('')
  const [gteText, setGteText] = useState('')
  const [lteText, setLteText] = useState('')
  const [remoteValues, setRemoteValues] = useState<string[] | null>(null)
  const [remoteTruncated, setRemoteTruncated] = useState(false)
  const [loadingRemote, setLoadingRemote] = useState(false)
  const [remoteError, setRemoteError] = useState('')

  const rangeEnabled = ['number', 'timestamp', 'date', 'datetime'].includes(String(semanticType || ''))

  useEffect(() => {
    if (!loadDistinctValues) return
    let cancelled = false
    setLoadingRemote(true)
    setRemoteError('')
    void loadDistinctValues()
      .then(result => {
        if (cancelled) return
        setRemoteValues(result.values)
        setRemoteTruncated(Boolean(result.truncated))
      })
      .catch((reason: any) => {
        if (cancelled) return
        setRemoteError(reason?.message || '加载可选值失败')
        setRemoteValues([])
      })
      .finally(() => {
        if (!cancelled) setLoadingRemote(false)
      })
    return () => { cancelled = true }
  }, [loadDistinctValues])

  const keys = (selectedKeys as string[]) || []
  const checkedValues = keys.filter(k => !isOperatorKey(String(k)))
  const containsKey = keys.find(k => String(k).startsWith(CONTAINS_PREFIX))
  const startsWithKey = keys.find(k => String(k).startsWith(STARTS_WITH_PREFIX))
  const gteKey = keys.find(k => String(k).startsWith(GTE_PREFIX))
  const lteKey = keys.find(k => String(k).startsWith(LTE_PREFIX))
  const activeContains = containsKey ? String(containsKey).slice(CONTAINS_PREFIX.length) : ''
  const activeStartsWith = startsWithKey ? String(startsWithKey).slice(STARTS_WITH_PREFIX.length) : ''
  const activeGte = gteKey ? String(gteKey).slice(GTE_PREFIX.length) : ''
  const activeLte = lteKey ? String(lteKey).slice(LTE_PREFIX.length) : ''

  const sourceValues = remoteValues ?? distinctValues
  const visibleValues = useMemo(() => {
    const q = listSearch.trim().toLowerCase()
    if (!q) return sourceValues
    return sourceValues.filter(v => {
      const label = v === NULL_FILTER_KEY ? '(NULL)' : v === '' ? '(空)' : v
      return label.toLowerCase().includes(q)
    })
  }, [sourceValues, listSearch])

  const withOperators = (base: string[]) => {
    const next = base.slice()
    const contains = (containsText || activeContains).trim()
    const starts = (startsWithText || activeStartsWith).trim()
    const gte = (gteText || activeGte).trim()
    const lte = (lteText || activeLte).trim()
    if (contains) next.push(`${CONTAINS_PREFIX}${contains}`)
    if (starts) next.push(`${STARTS_WITH_PREFIX}${starts}`)
    if (gte) next.push(`${GTE_PREFIX}${gte}`)
    if (lte) next.push(`${LTE_PREFIX}${lte}`)
    return next
  }

  return (
    <div className="dw-col-filter-dropdown" onKeyDown={e => e.stopPropagation()}>
      {loadingRemote ? (
        <div style={{ padding: '8px 0', textAlign: 'center' }}><Spin size="small" /></div>
      ) : null}
      {remoteError ? <div className="dw-col-filter-empty" style={{ color: '#ff4d4f' }}>{remoteError}</div> : null}
      {sourceValues.length > 0 ? (
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
              onChange={vals => setSelectedKeys(withOperators(vals as string[]))}
              style={{ display: 'flex', flexDirection: 'column', gap: 2, width: '100%' }}
            >
              {visibleValues.map(v => (
                <Checkbox key={v || '__empty__'} value={v} style={{ marginInlineStart: 0 }}>
                  <span className="dw-col-filter-value-label" title={v === NULL_FILTER_KEY ? '(NULL)' : v === '' ? '(空)' : v}>
                    {v === NULL_FILTER_KEY ? '(NULL)' : v === '' ? '(空)' : v}
                  </span>
                </Checkbox>
              ))}
            </Checkbox.Group>
            {visibleValues.length === 0 ? (
              <div className="dw-col-filter-empty">无匹配项</div>
            ) : null}
          </div>
          {remoteTruncated ? <Tag style={{ marginTop: 4 }}>仅显示前 {sourceValues.length} 个取值</Tag> : null}
          <div className="dw-col-filter-actions-inline">
            <button
              type="button"
              className="dw-col-filter-link"
              onClick={() => setSelectedKeys(withOperators(sourceValues))}
            >
              全选
            </button>
            <button
              type="button"
              className="dw-col-filter-link"
              onClick={() => setSelectedKeys(withOperators([]))}
            >
              清空
            </button>
          </div>
        </>
      ) : (!loadingRemote && !remoteError) ? (
        <div className="dw-col-filter-empty">暂无可选值</div>
      ) : null}
      <Input
        size="small"
        placeholder="包含即显示"
        value={containsText || activeContains}
        onChange={e => setContainsText(e.target.value)}
        onPressEnter={() => {
          setSelectedKeys(withOperators(checkedValues))
          confirm()
        }}
        style={{ marginTop: sourceValues.length > 0 ? 8 : 0, marginBottom: 8 }}
      />
      <Input
        size="small"
        placeholder="开头是"
        value={startsWithText || activeStartsWith}
        onChange={e => setStartsWithText(e.target.value)}
        onPressEnter={() => {
          setSelectedKeys(withOperators(checkedValues))
          confirm()
        }}
        style={{ marginBottom: 8 }}
      />
      {rangeEnabled ? (
        <Space size={6} style={{ marginBottom: 8, width: '100%' }}>
          <Input
            size="small"
            placeholder="≥"
            value={gteText || activeGte}
            onChange={e => setGteText(e.target.value)}
            style={{ width: 88 }}
          />
          <Input
            size="small"
            placeholder="≤"
            value={lteText || activeLte}
            onChange={e => setLteText(e.target.value)}
            style={{ width: 88 }}
          />
        </Space>
      ) : null}
      <Space>
        <Button
          type="primary"
          size="small"
          onClick={() => {
            setSelectedKeys(withOperators(checkedValues))
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
            setStartsWithText('')
            setGteText('')
            setLteText('')
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

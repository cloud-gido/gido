/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useMemo, useState } from 'react'
import { Alert, Drawer, Empty, Radio, Select, Space, Typography } from 'antd'
import {
  buildQueryChartPoints,
  isNumericChartField,
  suggestQueryChartAxes,
  type QueryChartField,
} from '../utils/queryResultChart'

type ChartKind = 'bar' | 'line'

function SvgChart({
  points,
  kind,
}: {
  points: Array<{ category: string; value: number }>
  kind: ChartKind
}) {
  const width = 640
  const height = 320
  const pad = { top: 16, right: 16, bottom: 64, left: 52 }
  const innerW = width - pad.left - pad.right
  const innerH = height - pad.top - pad.bottom
  const max = Math.max(...points.map(item => item.value), 1)
  const gap = kind === 'bar' ? 0.28 : 0
  const slot = innerW / Math.max(points.length, 1)
  const barW = Math.max(3, Math.min(28, slot * (1 - gap)))
  const labelEvery = points.length > 16 ? Math.ceil(points.length / 12) : 1

  const coords = points.map((point, index) => {
    const x = pad.left + slot * index + slot / 2
    const y = pad.top + innerH * (1 - point.value / max)
    return { ...point, x, y, barX: x - barW / 2, index }
  })

  const linePath = coords
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x},${point.y}`)
    .join(' ')

  const formatTick = (n: number) => {
    if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (Math.abs(n) >= 10_000) return `${(n / 1000).toFixed(1)}k`
    if (Number.isInteger(n)) return String(n)
    return n.toFixed(1)
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="查询结果图表">
      <line
        x1={pad.left}
        y1={pad.top}
        x2={pad.left}
        y2={pad.top + innerH}
        stroke="#d9d9d9"
      />
      <line
        x1={pad.left}
        y1={pad.top + innerH}
        x2={pad.left + innerW}
        y2={pad.top + innerH}
        stroke="#d9d9d9"
      />
      {[0, 0.5, 1].map(ratio => {
        const y = pad.top + innerH * (1 - ratio)
        const label = max * ratio
        return (
          <g key={ratio}>
            <line x1={pad.left} y1={y} x2={pad.left + innerW} y2={y} stroke="#f5f5f5" />
            <text x={pad.left - 8} y={y + 4} textAnchor="end" fontSize={11} fill="#8c8c8c">
              {formatTick(label)}
            </text>
          </g>
        )
      })}
      {kind === 'bar'
        ? coords.map(point => (
          <rect
            key={`${point.category}-${point.index}`}
            x={point.barX}
            y={point.y}
            width={barW}
            height={Math.max(1, pad.top + innerH - point.y)}
            fill="#1677ff"
            rx={2}
          >
            <title>{`${point.category}: ${point.value}`}</title>
          </rect>
        ))
        : (
          <>
            <path d={linePath} fill="none" stroke="#1677ff" strokeWidth={2} />
            {coords.map(point => (
              <circle key={`${point.category}-${point.index}`} cx={point.x} cy={point.y} r={3} fill="#1677ff">
                <title>{`${point.category}: ${point.value}`}</title>
              </circle>
            ))}
          </>
        )}
      {coords.map(point => {
        if (point.index % labelEvery !== 0 && point.index !== coords.length - 1) return null
        const label = point.category.length > 10 ? `${point.category.slice(0, 10)}…` : point.category
        return (
          <text
            key={`${point.category}-label-${point.index}`}
            x={point.x}
            y={height - 18}
            textAnchor="middle"
            fontSize={10}
            fill="#8c8c8c"
            transform={`rotate(-32 ${point.x} ${height - 18})`}
          >
            {label}
          </text>
        )
      })}
    </svg>
  )
}

export default function QueryResultChartDrawer({
  open,
  onClose,
  rows,
  fields,
}: {
  open: boolean
  onClose: () => void
  rows: Array<Record<string, unknown>>
  fields: QueryChartField[]
}) {
  const suggested = useMemo(() => suggestQueryChartAxes(fields), [fields])
  const [category, setCategory] = useState<string | null>(suggested.category)
  const [value, setValue] = useState<string | null>(suggested.value)
  const [kind, setKind] = useState<ChartKind>('bar')

  useEffect(() => {
    if (!open) return
    setCategory(suggested.category)
    setValue(suggested.value)
  }, [open, suggested.category, suggested.value])

  const categoryOptions = fields.map(field => ({
    value: field.name,
    label: field.name,
  }))
  const valueOptions = useMemo(() => {
    const numeric = fields.filter(isNumericChartField)
    const source = numeric.length ? numeric : fields
    return source.map(field => ({
      value: field.name,
      label: field.name,
    }))
  }, [fields])

  const points = useMemo(() => {
    if (!category || !value) return []
    return buildQueryChartPoints(rows, category, value, 100)
  }, [rows, category, value])

  const hasNumeric = fields.some(isNumericChartField)

  return (
    <Drawer
      title="结果图表（当前视口）"
      width={720}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="仅基于当前视口已显示的行做预览，不是全量分析结果。完整分析请导出后使用外部工具。"
      />
      <Space wrap style={{ marginBottom: 12 }}>
        <span>分类</span>
        <Select
          style={{ minWidth: 160 }}
          value={category ?? undefined}
          options={categoryOptions}
          onChange={setCategory}
          placeholder="选择分类列"
          showSearch
          optionFilterProp="label"
        />
        <span>数值</span>
        <Select
          style={{ minWidth: 160 }}
          value={value ?? undefined}
          options={valueOptions}
          onChange={setValue}
          placeholder="选择数值列"
          showSearch
          optionFilterProp="label"
        />
        <Radio.Group
          value={kind}
          optionType="button"
          buttonStyle="solid"
          options={[
            { label: '柱状', value: 'bar' },
            { label: '折线', value: 'line' },
          ]}
          onChange={event => setKind(event.target.value)}
        />
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
        视口 {rows.length} 行 · 同分类求和
        {points.length ? ` · 展示 ${points.length} 个分类` : ''}
        {!hasNumeric ? ' · 未识别到数值列，可手动选择可解析为数字的列' : ''}
      </Typography.Paragraph>
      {!category || !value ? (
        <Empty description="请选择分类列与数值列" />
      ) : !points.length ? (
        <Empty description="当前视口没有可用的数值点，请换列或检查数据是否为数字" />
      ) : (
        <SvgChart points={points} kind={kind} />
      )}
    </Drawer>
  )
}

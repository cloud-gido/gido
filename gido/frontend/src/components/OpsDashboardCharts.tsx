/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Card, Col, Row, Empty } from 'antd'

type TrendPoint = { date: string; total: number; success: number; failed: number }
type StatusSlice = { status: string; count: number }

const STATUS_COLORS: Record<string, string> = {
  success: '#52c41a',
  failed: '#ff4d4f',
  running: '#1677ff',
  pending: '#faad14',
  killed: '#8c8c8c',
}

const STATUS_LABELS: Record<string, string> = {
  success: '成功',
  failed: '失败',
  running: '运行中',
  pending: '等待',
  killed: '终止',
}

function shortDate(iso: string) {
  const p = iso.split('-')
  return p.length >= 3 ? `${p[1]}/${p[2]}` : iso
}

function TrendChart({ data }: { data: TrendPoint[] }) {
  const w = 520
  const h = 180
  const pad = { t: 16, r: 12, b: 28, l: 36 }
  const innerW = w - pad.l - pad.r
  const innerH = h - pad.t - pad.b
  const maxY = Math.max(1, ...data.map(d => d.total))
  const step = data.length > 1 ? innerW / (data.length - 1) : innerW

  const points = (key: keyof TrendPoint) =>
    data
      .map((d, i) => {
        const x = pad.l + i * step
        const y = pad.t + innerH - (Number(d[key]) / maxY) * innerH
        return `${x},${y}`
      })
      .join(' ')

  if (!data.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无趋势数据" />

  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img" aria-label="近7日实例趋势">
      {[0, 0.25, 0.5, 0.75, 1].map(r => {
        const y = pad.t + innerH * (1 - r)
        return (
          <g key={r}>
            <line x1={pad.l} y1={y} x2={w - pad.r} y2={y} stroke="#f0f0f0" />
            <text x={pad.l - 6} y={y + 4} textAnchor="end" fontSize="10" fill="#999">
              {Math.round(maxY * r)}
            </text>
          </g>
        )
      })}
      <polyline fill="none" stroke="#1677ff" strokeWidth="2" points={points('total')} />
      <polyline fill="none" stroke="#52c41a" strokeWidth="2" points={points('success')} />
      <polyline fill="none" stroke="#ff4d4f" strokeWidth="2" points={points('failed')} />
      {data.map((d, i) => (
        <text
          key={d.date}
          x={pad.l + i * step}
          y={h - 8}
          textAnchor="middle"
          fontSize="10"
          fill="#666"
        >
          {shortDate(d.date)}
        </text>
      ))}
      <g transform={`translate(${pad.l}, ${pad.t})`}>
        <rect x={0} y={0} width="10" height="3" fill="#1677ff" />
        <text x="14" y="4" fontSize="11" fill="#666">总量</text>
        <rect x="48" y={0} width="10" height="3" fill="#52c41a" />
        <text x="62" y="4" fontSize="11" fill="#666">成功</text>
        <rect x="96" y={0} width="10" height="3" fill="#ff4d4f" />
        <text x="110" y="4" fontSize="11" fill="#666">失败</text>
      </g>
    </svg>
  )
}

function StatusDonut({ data }: { data: StatusSlice[] }) {
  const filtered = data.filter(d => d.count > 0)
  const total = filtered.reduce((s, d) => s + d.count, 0)
  const size = 160
  const r = 52
  const cx = size / 2
  const cy = size / 2
  const C = 2 * Math.PI * r

  if (!total) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无状态分布" />

  let offset = 0
  const arcs = filtered.map(d => {
    const frac = d.count / total
    const dash = frac * C
    const el = (
      <circle
        key={d.status}
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke={STATUS_COLORS[d.status] || '#d9d9d9'}
        strokeWidth="18"
        strokeDasharray={`${dash} ${C - dash}`}
        strokeDashoffset={-offset}
        transform={`rotate(-90 ${cx} ${cy})`}
      />
    )
    offset += dash
    return el
  })

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 20, flexWrap: 'wrap' }}>
      <svg width={size} height={size} role="img" aria-label="实例状态分布">
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#f5f5f5" strokeWidth="18" />
        {arcs}
        <text x={cx} y={cy - 4} textAnchor="middle" fontSize="20" fontWeight="600" fill="#262626">
          {total}
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" fontSize="11" fill="#8c8c8c">
          实例
        </text>
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {filtered.map(d => (
          <div key={d.status} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
            <span
              style={{
                width: 10,
                height: 10,
                borderRadius: 2,
                background: STATUS_COLORS[d.status] || '#d9d9d9',
                display: 'inline-block',
              }}
            />
            <span style={{ color: '#595959', minWidth: 48 }}>{STATUS_LABELS[d.status] || d.status}</span>
            <span style={{ fontWeight: 600 }}>{d.count}</span>
            <span style={{ color: '#999' }}>({Math.round((d.count / total) * 100)}%)</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function OpsDashboardCharts({
  dailyTrend,
  statusDistribution,
}: {
  dailyTrend?: TrendPoint[]
  statusDistribution?: StatusSlice[]
}) {
  return (
    <Row gutter={16} style={{ marginBottom: 24 }}>
      <Col xs={24} lg={14}>
        <Card title="近 7 日实例趋势" size="small" styles={{ body: { paddingTop: 8 } }}>
          <TrendChart data={dailyTrend || []} />
        </Card>
      </Col>
      <Col xs={24} lg={10}>
        <Card title="实例状态分布" size="small">
          <StatusDonut data={statusDistribution || []} />
        </Card>
      </Col>
    </Row>
  )
}

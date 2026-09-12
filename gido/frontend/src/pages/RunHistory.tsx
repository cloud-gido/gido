/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * 运行历史：数据开发试跑与数据探查的交互式执行记录
 */
import { useEffect, useState } from 'react'
import { Table, Tag, Select, Button, Switch, message, Tooltip } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { adhocRunsApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone, formatInTimeZoneCompact } from '../utils/datetime'
import { R } from '../routes'

const STATUS_COLOR: Record<string, string> = {
  success: 'green',
  failed: 'red',
  running: 'blue',
}

const STATUS_LABEL: Record<string, string> = {
  success: '成功',
  failed: '失败',
  running: '运行中',
}

const SOURCE_LABEL: Record<string, string> = {
  studio: '数据开发',
  probe: '数据探查',
}

export default function RunHistoryPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const navigate = useNavigate()

  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [source, setSource] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [mineOnly, setMineOnly] = useState(true)
  const [canViewAll, setCanViewAll] = useState(false)
  const [allowedSources, setAllowedSources] = useState<string[]>(['studio', 'probe'])
  const [loading, setLoading] = useState(false)

  const openDetail = (id: number) => navigate(`${R.batch.runHistory}/${id}`)

  const load = async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const res: any = await adhocRunsApi.list(wsId, {
        page,
        page_size: 20,
        source: source || undefined,
        status: status || undefined,
        mine_only: mineOnly,
      })
      setItems(res.items || [])
      setTotal(res.total || 0)
      setCanViewAll(!!res.can_view_all)
      if (Array.isArray(res.allowed_sources)) setAllowedSources(res.allowed_sources)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [wsId, page, source, status, mineOnly])

  const columns = [
    {
      title: '名称',
      width: 220,
      ellipsis: true,
      render: (_: unknown, row: any) => {
        const title = row.object_name || SOURCE_LABEL[row.source] || '未命名'
        return (
          <div
            style={{ minWidth: 0, cursor: 'pointer' }}
            onClick={() => openDetail(row.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                openDetail(row.id)
              }
            }}
            role="link"
            tabIndex={0}
          >
            <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {title}
            </div>
            <div style={{ color: '#8c8c8c', fontSize: 12 }}>
              {SOURCE_LABEL[row.source] || row.source}
            </div>
          </div>
        )
      },
    },
    {
      title: '语句摘要',
      width: 200,
      ellipsis: true,
      render: (_: unknown, row: any) => {
        const summary = row.sql_summary
          || (row.error_message ? String(row.error_message).slice(0, 48) : '')
        if (!summary) return <span style={{ color: '#bbb' }}>—</span>
        return (
          <Tooltip title={row.sql_summary || row.error_message || undefined}>
            <span
              style={{
                color: row.status === 'failed' && !row.sql_summary ? '#cf1322' : '#595959',
                fontSize: 12.5,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              }}
            >
              {summary}
            </span>
          </Tooltip>
        )
      },
    },
    {
      title: '数据源',
      dataIndex: 'datasource_name',
      width: 110,
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 80,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] || 'default'}>{STATUS_LABEL[s] || s}</Tag>
      ),
    },
    {
      title: '执行人',
      dataIndex: 'triggered_by_name',
      width: 96,
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: '行数',
      dataIndex: 'rows_returned',
      width: 64,
      render: (v: number, row: any) =>
        row.result_truncated ? `${v}+` : (v ?? 0),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 64,
      render: (ms: number) => (ms != null ? `${(ms / 1000).toFixed(1)}s` : '—'),
    },
    {
      title: '时间',
      dataIndex: 'started_at',
      width: 128,
      render: (v: string) => (
        v ? (
          <Tooltip title={formatInTimeZone(v, displayTz)}>
            <span style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12.5 }}>
              {formatInTimeZoneCompact(v, displayTz)}
            </span>
          </Tooltip>
        ) : '—'
      ),
    },
    {
      title: '操作',
      width: 72,
      render: (_: unknown, row: any) => (
        <Button type="link" size="small" onClick={() => openDetail(row.id)}>
          详情
        </Button>
      ),
    },
  ]

  return (
    <div>
      <h2>运行历史</h2>
      <p style={{ color: '#666', marginBottom: 16 }}>
        数据开发试跑与数据探查的交互式执行记录。已上线工作流的调度实例请到「实例中心」。
      </p>
      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Select
          placeholder="来源"
          allowClear
          style={{ width: 140 }}
          value={source}
          onChange={v => { setSource(v); setPage(1) }}
          options={allowedSources.map(s => ({ label: SOURCE_LABEL[s] || s, value: s }))}
        />
        <Select
          placeholder="状态"
          allowClear
          style={{ width: 120 }}
          value={status}
          onChange={v => { setStatus(v); setPage(1) }}
          options={[
            { label: '成功', value: 'success' },
            { label: '失败', value: 'failed' },
          ]}
        />
        {canViewAll && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Switch checked={mineOnly} onChange={v => { setMineOnly(v); setPage(1) }} />
            仅看我的
          </span>
        )}
        <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>
      </div>
      <Table
        loading={loading}
        dataSource={items}
        columns={columns}
        rowKey="id"
        tableLayout="fixed"
        scroll={{ x: 980 }}
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
      />
    </div>
  )
}

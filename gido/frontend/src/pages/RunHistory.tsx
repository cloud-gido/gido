/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * 运行历史：数据开发试跑与数据探查的交互式执行记录
 */
import { useEffect, useState } from 'react'
import { Table, Tag, Select, Button, Switch, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { adhocRunsApi } from '../api'
import { useAppStore } from '../store'
import { R } from '../routes'
import SqlStatementSummaryCell from '../components/SqlStatementSummaryCell'
import WorkspaceTime from '../components/WorkspaceTime'
import { useResizableTableColumns } from '../hooks/useResizableTableColumns'

const STATUS_COLOR: Record<string, string> = {
  queued: 'blue',
  success: 'green',
  failed: 'red',
  running: 'blue',
  cancel_requested: 'orange',
  cancelled: 'default',
  timed_out: 'red',
}

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  success: '成功',
  failed: '失败',
  running: '运行中',
  cancel_requested: '停止中',
  cancelled: '已停止',
  timed_out: '已超时',
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

  const columnsBase = [
    {
      title: '名称',
      key: 'name',
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
      key: 'sql_summary',
      width: 200,
      ellipsis: true,
      render: (_: unknown, row: any) => (
        <SqlStatementSummaryCell
          summary={row.sql_summary}
          preview={row.sql_preview}
          fallback={row.error_message ? String(row.error_message).slice(0, 72) : null}
          danger={row.status === 'failed' && !row.sql_summary}
        />
      ),
    },
    {
      title: '数据源',
      dataIndex: 'datasource_name',
      key: 'datasource_name',
      width: 110,
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] || 'default'}>{STATUS_LABEL[s] || s}</Tag>
      ),
    },
    {
      title: '执行人',
      dataIndex: 'triggered_by_name',
      key: 'triggered_by_name',
      width: 96,
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: '行数',
      dataIndex: 'rows_returned',
      key: 'rows_returned',
      width: 64,
      render: (v: number, row: any) =>
        row.result_truncated ? `${v}+` : (v ?? 0),
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 64,
      render: (ms: number) => (ms != null ? `${(ms / 1000).toFixed(1)}s` : '—'),
    },
    {
      title: '时间',
      dataIndex: 'started_at',
      key: 'started_at',
      width: 128,
      render: (v: string) => <WorkspaceTime value={v} timeZone={displayTz} />,
    },
    {
      title: '操作',
      key: 'actions',
      width: 72,
      render: (_: unknown, row: any) => (
        <Button type="link" size="small" onClick={() => openDetail(row.id)}>
          详情
        </Button>
      ),
    },
  ]

  const columns = useResizableTableColumns(columnsBase, {
    storageKey: wsId ? `gido.batch.runHistory.cols.w${wsId}` : undefined,
    defaultWidths: {
      name: 220,
      sql_summary: 200,
      datasource_name: 110,
      status: 80,
      triggered_by_name: 96,
      rows_returned: 64,
      duration_ms: 64,
      started_at: 128,
      actions: 72,
    },
  })

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
        className="dw-resizable-table"
        tableLayout="fixed"
        scroll={{ x: 980 }}
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
      />
    </div>
  )
}

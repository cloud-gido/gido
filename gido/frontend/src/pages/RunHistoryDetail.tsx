/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * 运行历史详情：SQL + 结果预览
 */
import { useEffect, useState } from 'react'
import { Button, Card, Descriptions, Space, Tag, message, Alert, Skeleton } from 'antd'
import { ArrowLeftOutlined, CopyOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { adhocRunsApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'
import DwMonacoEditor from '../components/DwMonacoEditor'
import { useInteractiveRun } from '../hooks/useInteractiveRun'
import { R } from '../routes'
import InteractiveRunDock from '../components/InteractiveRunDock'

const STATUS_COLOR: Record<string, string> = {
  queued: 'blue',
  cancel_requested: 'orange',
  success: 'green',
  failed: 'red',
  running: 'blue',
  cancelled: 'default',
  timed_out: 'red',
}

const SOURCE_LABEL: Record<string, string> = {
  studio: '数据开发',
  probe: '数据探查',
}

export default function RunHistoryDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { currentWorkspace } = useAppStore()
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [row, setRow] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const liveRun = useInteractiveRun({
    workspaceId: row?.workspace_id,
    nodeId: row?.node_id,
    source: row?.source === 'probe' ? 'probe' : 'studio',
    autoRecover: false,
    recoveryKey: `history:${id ?? 'none'}`,
  })

  useEffect(() => {
    const runId = Number(id)
    if (!runId) return
    setLoading(true)
    setRow(null)
    adhocRunsApi
      .get(runId)
      .then((res: any) => setRow(res))
      .catch((e: any) => message.error(e?.response?.data?.detail || e?.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [id])

  useEffect(() => {
    if (row?.id) {
      liveRun.attach(Number(row.id))
    }
  }, [row?.id])

  useEffect(() => {
    if (!row?.id || liveRun.version < 0) return
    adhocRunsApi.get(Number(row.id)).then(setRow).catch(() => undefined)
  }, [row?.id, liveRun.version])

  const preview = row?.result_preview
  const copySql = async () => {
    if (!row?.sql_text) return
    try {
      await navigator.clipboard.writeText(row.sql_text)
      message.success('已复制 SQL')
    } catch {
      message.error('复制失败')
    }
  }

  const openInStudio = () => {
    if (row?.node_id) {
      navigate(`${R.batch.studio}?node_id=${row.node_id}`)
      return
    }
    navigate(R.batch.studio)
  }

  const openInProbe = () => {
    navigate(R.batch.probe)
  }

  if (!row && !loading) {
    return (
      <div>
        <Button icon={<ArrowLeftOutlined />} type="link" onClick={() => navigate(R.batch.runHistory)}>
          返回运行历史
        </Button>
        <Alert type="warning" message="记录不存在" />
      </div>
    )
  }

  const metaReady = Boolean(row)
  const sqlReady = Boolean(row?.sql_text)
  const resultReady = Boolean(preview?.columns?.length) || (metaReady && !loading)

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(R.batch.runHistory)}>
          返回
        </Button>
        <h2 style={{ margin: 0 }}>运行详情 #{row?.id ?? id}</h2>
        {row?.status && <Tag color={STATUS_COLOR[row.status] || 'default'}>{row.status}</Tag>}
      </Space>

      <Card size="small" style={{ marginBottom: 16 }}>
        {!metaReady ? (
          <Skeleton active paragraph={{ rows: 3 }} title={false} />
        ) : (
          <>
            <Descriptions column={3} size="small">
              <Descriptions.Item label="来源">{SOURCE_LABEL[row?.source] || row?.source}</Descriptions.Item>
              <Descriptions.Item label="对象">{row?.object_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="数据源">{row?.datasource_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="执行人">{row?.triggered_by_name || '—'}</Descriptions.Item>
              <Descriptions.Item label="开始时间">{formatInTimeZone(row?.started_at, displayTz)}</Descriptions.Item>
              <Descriptions.Item label="结束时间">{formatInTimeZone(row?.finished_at, displayTz)}</Descriptions.Item>
              <Descriptions.Item label="耗时">
                {row?.duration_ms != null ? `${(row.duration_ms / 1000).toFixed(2)}s` : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="返回行数">
                {row?.rows_returned ?? 0}
                {row?.result_preview?.truncated ? '（已截断预览）' : ''}
              </Descriptions.Item>
            </Descriptions>
            <Space style={{ marginTop: 8 }}>
              {row?.source === 'studio' && (
                <Button size="small" onClick={openInStudio}>在数据开发打开</Button>
              )}
              {row?.source === 'probe' && (
                <Button size="small" onClick={openInProbe}>打开数据探查</Button>
              )}
            </Space>
          </>
        )}
      </Card>

      <Card
        title="执行语句"
        size="small"
        style={{ marginBottom: 16 }}
        extra={
          <Button size="small" icon={<CopyOutlined />} onClick={copySql} disabled={!row?.sql_text}>
            复制
          </Button>
        }
      >
        {!sqlReady ? (
          loading ? (
            <Skeleton active paragraph={{ rows: 6 }} title={false} />
          ) : (
            <div style={{ color: '#999', padding: '8px 0' }}>（无）</div>
          )
        ) : (
          <DwMonacoEditor
            value={row.sql_text}
            readOnly
            height={Math.min(320, Math.max(160, String(row.sql_text).split('\n').length * 18 + 24))}
            findBar={false}
            options={{
              lineNumbers: 'on',
              wordWrap: 'on',
              folding: false,
              renderLineHighlight: 'none',
              overviewRulerLanes: 0,
              scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
            }}
          />
        )}
      </Card>

      {row?.error_message && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="错误信息" description={row.error_message} />
      )}

      <Card title="实时运行明细" size="small" styles={{ body: { padding: 0, minHeight: 420 } }}>
        {loading && !resultReady ? (
          <div style={{ padding: 16 }}>
            <Skeleton active paragraph={{ rows: 8 }} title={false} />
          </div>
        ) : liveRun.runId === Number(row?.id) ? (
          <div style={{ height: 420, display: 'flex', flexDirection: 'column' }}>
            <InteractiveRunDock
              run={liveRun}
              scopeKey={`history:${row?.workspace_id}:${row?.id}`}
            />
          </div>
        ) : (
          <div style={{ padding: 16, color: '#999' }}>正在加载运行日志与语句结果…</div>
        )}
      </Card>
    </div>
  )
}

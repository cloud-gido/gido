/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * 运行历史详情：SQL + 结果预览
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Descriptions, Space, Tag, Table, Typography, message, Alert } from 'antd'
import { ArrowLeftOutlined, CopyOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { adhocRunsApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import { R } from '../routes'

const { Paragraph, Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  success: 'green',
  failed: 'red',
  running: 'blue',
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
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const runId = Number(id)
    if (!runId) return
    setLoading(true)
    adhocRunsApi
      .get(runId)
      .then((res: any) => setRow(res))
      .catch((e: any) => message.error(e?.response?.data?.detail || e?.message || '加载失败'))
      .finally(() => setLoading(false))
  }, [id])

  const preview = row?.result_preview
  const tableBundle = useMemo(() => {
    if (!preview?.columns?.length) {
      return { dataSource: [] as ReturnType<typeof rowsToRecordDataSource>, tableColumns: buildQueryTableColumns([]) }
    }
    return {
      dataSource: rowsToRecordDataSource(preview.columns, preview.rows || []),
      tableColumns: buildQueryTableColumns(preview.columns as string[]),
    }
  }, [preview])

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

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(R.batch.runHistory)}>
          返回
        </Button>
        <h2 style={{ margin: 0 }}>运行详情 #{row?.id ?? id}</h2>
        {row?.status && <Tag color={STATUS_COLOR[row.status] || 'default'}>{row.status}</Tag>}
      </Space>

      <Card loading={loading} size="small" style={{ marginBottom: 16 }}>
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
        <Paragraph>
          <Text code style={{ whiteSpace: 'pre-wrap', display: 'block' }}>
            {row?.sql_text || '（无）'}
          </Text>
        </Paragraph>
      </Card>

      {row?.error_message && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="错误信息" description={row.error_message} />
      )}

      {row?.log_content && (
        <Card title="执行日志" size="small" style={{ marginBottom: 16 }}>
          <pre style={{ margin: 0, maxHeight: 240, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
            {row.log_content}
          </pre>
        </Card>
      )}

      <Card title="查询结果" size="small">
        {preview?.columns?.length ? (
          <Table
            size="small"
            scroll={{ x: true }}
            pagination={{ pageSize: 50, showSizeChanger: true }}
            dataSource={tableBundle.dataSource}
            columns={tableBundle.tableColumns}
            rowKey={(_, i) => String(i)}
          />
        ) : (
          <span style={{ color: '#999' }}>无结果集（非查询语句、失败或未返回行）</span>
        )}
      </Card>
    </div>
  )
}

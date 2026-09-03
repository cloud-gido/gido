/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 发布审批：Drawer 内只读预览 + 可选基准对比，不离开审批页。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Space, Spin, Table, Tabs, Tag, Typography, message,
} from 'antd'
import { ExportOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { approvalApi } from '../api'
import { APPROVAL_ACTION_LABEL, APPROVAL_RESOURCE_LABEL } from '../approvalLabels'
import {
  approvalResourceOpenLabel,
  approvalResourceOpenPath,
} from '../utils/approvalOpenTarget'
import { formatInTimeZone } from '../utils/datetime'
import DwMonacoEditor from './DwMonacoEditor'

const { Text, Paragraph } = Typography

type DagNodeRow = { node_id: number; name: string; node_type: string }

type Props = {
  approvalId: number | null
  open: boolean
  onClose: () => void
  displayTz?: string
}

function ScriptPane({ value, height = 320 }: { value: string; height?: number }) {
  return (
    <DwMonacoEditor
      height={height}
      language="sql"
      value={value || '-- 空脚本'}
      readOnly
      findBar={false}
      style={{ borderColor: '#f0f0f0' }}
    />
  )
}

function JsonPane({ value }: { value: unknown }) {
  const text = useMemo(() => {
    try {
      return JSON.stringify(value ?? {}, null, 2)
    } catch {
      return String(value ?? '')
    }
  }, [value])
  return (
    <pre style={{
      margin: 0,
      padding: 12,
      background: '#fafafa',
      borderRadius: 6,
      maxHeight: 280,
      overflow: 'auto',
      fontSize: 12,
    }}
    >
      {text}
    </pre>
  )
}

export default function ApprovalResourcePreviewDrawer({
  approvalId,
  open,
  onClose,
  displayTz = 'Asia/Shanghai',
}: Props) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    if (!open || approvalId == null) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    approvalApi.preview(approvalId)
      .then((res: any) => {
        if (!cancelled) setData(res)
      })
      .catch((e: any) => {
        if (!cancelled) {
          message.error(e?.response?.data?.detail || '加载预览失败')
          onClose()
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open, approvalId, onClose])

  const approval = data?.approval
  const preview = data?.preview
  const openPath = approval ? approvalResourceOpenPath(approval) : null

  const scriptTabs = useMemo(() => {
    if (!preview) return []
    const pendingScript = preview.pending?.script_content
    const baselineScript = preview.baseline?.script_content
    if (pendingScript == null && baselineScript == null) return []
    if (preview.has_diff && baselineScript != null) {
      return [
        { key: 'pending', label: '本次提交', children: <ScriptPane value={pendingScript || ''} /> },
        {
          key: 'baseline',
          label: preview.baseline_label || '对比基准',
          children: <ScriptPane value={baselineScript || ''} />,
        },
      ]
    }
    return [{ key: 'pending', label: '脚本内容', children: <ScriptPane value={pendingScript || ''} /> }]
  }, [preview])

  const dagNodes: DagNodeRow[] = preview?.pending?.dag?.nodes || []
  const baselineDagNodes: DagNodeRow[] = preview?.baseline?.dag?.nodes || []

  const body = (() => {
    if (loading) {
      return <div style={{ textAlign: 'center', padding: 48 }}><Spin /></div>
    }
    if (!approval || !preview) return null

    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {approval.submit_note ? (
          <Alert type="info" showIcon message="提交说明" description={approval.submit_note} />
        ) : null}

        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="类型">
            {APPROVAL_RESOURCE_LABEL[approval.resource_type] || approval.resource_type}
          </Descriptions.Item>
          <Descriptions.Item label="发布动作">
            {APPROVAL_ACTION_LABEL[approval.action] || approval.action}
          </Descriptions.Item>
          <Descriptions.Item label="资源名" span={2}>
            {approval.resource_name}
          </Descriptions.Item>
          <Descriptions.Item label="提交人">{approval.submitted_by_username || '—'}</Descriptions.Item>
          <Descriptions.Item label="提交时间">
            {approval.submitted_at ? formatInTimeZone(approval.submitted_at, displayTz) : '—'}
          </Descriptions.Item>
        </Descriptions>

        {preview.kind === 'studio_node' && (
          <Descriptions size="small" column={2} title="脚本摘要">
            <Descriptions.Item label="节点类型">{preview.summary?.node_type}</Descriptions.Item>
            <Descriptions.Item label="生产状态">
              {preview.summary?.is_published ? <Tag color="green">已提交过</Tag> : <Tag>草稿</Tag>}
            </Descriptions.Item>
          </Descriptions>
        )}

        {preview.kind === 'workflow' && (
          <>
            <Descriptions size="small" column={2} title="调度">
              <Descriptions.Item label="调度类型">{preview.summary?.schedule_type || '—'}</Descriptions.Item>
              <Descriptions.Item label="Cron">{preview.summary?.cron_expression || '—'}</Descriptions.Item>
              <Descriptions.Item label="生命周期">{preview.summary?.status || '—'}</Descriptions.Item>
              <Descriptions.Item label="节点数">{preview.pending?.dag?.node_count ?? 0}</Descriptions.Item>
            </Descriptions>
            {preview.has_diff && preview.baseline ? (
              <Alert
                type="warning"
                showIcon
                message={`与${preview.baseline_label}存在差异（节点 ${preview.pending?.dag?.node_count} → 基准 ${preview.baseline?.dag?.node_count}）`}
              />
            ) : null}
            <Table<DagNodeRow>
              size="small"
              pagination={false}
              rowKey="node_id"
              dataSource={dagNodes}
              columns={[
                { title: '节点', dataIndex: 'name', ellipsis: true },
                { title: '类型', dataIndex: 'node_type', width: 88 },
                { title: 'ID', dataIndex: 'node_id', width: 72 },
              ]}
            />
            {preview.has_diff && baselineDagNodes.length > 0 ? (
              <>
                <Text type="secondary">{preview.baseline_label} 节点清单</Text>
                <Table<DagNodeRow>
                  size="small"
                  pagination={false}
                  rowKey="node_id"
                  dataSource={baselineDagNodes}
                  columns={[
                    { title: '节点', dataIndex: 'name', ellipsis: true },
                    { title: '类型', dataIndex: 'node_type', width: 88 },
                    { title: 'ID', dataIndex: 'node_id', width: 72 },
                  ]}
                />
              </>
            ) : null}
          </>
        )}

        {preview.kind === 'stream_job' && (
          <Descriptions size="small" column={2} title="实时作业">
            <Descriptions.Item label="作业类型">{preview.summary?.job_type}</Descriptions.Item>
            <Descriptions.Item label="发布版本">
              {preview.pending?.release_version != null ? `v${preview.pending.release_version}` : '—'}
            </Descriptions.Item>
            {preview.pending?.release_note ? (
              <Descriptions.Item label="发布说明" span={2}>
                {preview.pending.release_note}
              </Descriptions.Item>
            ) : null}
          </Descriptions>
        )}

        {preview.kind === 'data_service_api' && (
          <>
            <Descriptions size="small" column={2} title="API">
              <Descriptions.Item label="编码">{preview.summary?.api_code}</Descriptions.Item>
              <Descriptions.Item label="模式">{preview.summary?.mode}</Descriptions.Item>
              <Descriptions.Item label="当前状态">{preview.summary?.status}</Descriptions.Item>
              <Descriptions.Item label="版本">v{preview.summary?.version ?? 1}</Descriptions.Item>
            </Descriptions>
            {approval.action === 'offline_api' ? (
              <Alert type="warning" showIcon message="本次审批为 API 下线" />
            ) : preview.has_diff ? (
              <Tabs
                items={[
                  { key: 'pending', label: '待发布', children: <JsonPane value={preview.pending} /> },
                  {
                    key: 'baseline',
                    label: preview.baseline_label || '线上版本',
                    children: <JsonPane value={preview.baseline} />,
                  },
                ]}
              />
            ) : (
              <JsonPane value={preview.pending} />
            )}
            {preview.pending?.sql_template ? (
              <>
                <Text strong>SQL 模板</Text>
                <ScriptPane value={String(preview.pending.sql_template)} height={240} />
              </>
            ) : null}
          </>
        )}

        {scriptTabs.length > 0 ? (
          scriptTabs.length > 1 ? <Tabs items={scriptTabs} /> : scriptTabs[0]?.children
        ) : null}

        {openPath ? (
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            需要编辑或查看完整上下文时，可在新页打开；审批仍保留在当前列表。
          </Paragraph>
        ) : null}
      </Space>
    )
  })()

  return (
    <Drawer
      title={approval ? `审批预览 — ${approval.resource_name}` : '审批预览'}
      width={Math.min(920, typeof window !== 'undefined' ? window.innerWidth - 48 : 920)}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={openPath ? (
        <Link to={openPath} target="_blank" rel="noopener noreferrer">
          <Button type="link" icon={<ExportOutlined />} style={{ padding: 0 }}>
            {approvalResourceOpenLabel(approval?.resource_type)}
          </Button>
        </Link>
      ) : null}
      footer={openPath ? (
        <Space>
          <Button onClick={onClose}>关闭</Button>
          <Link to={openPath} target="_blank" rel="noopener noreferrer">
            <Button type="primary" icon={<ExportOutlined />}>
              {approvalResourceOpenLabel(approval?.resource_type)}
            </Button>
          </Link>
        </Space>
      ) : (
        <Button onClick={onClose}>关闭</Button>
      )}
    >
      {body}
    </Drawer>
  )
}

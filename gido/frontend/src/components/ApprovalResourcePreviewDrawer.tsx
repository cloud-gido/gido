/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 发布审批：Drawer 内只读预览 + 可选基准对比，不离开审批页。
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Descriptions, Drawer, Space, Spin, Tabs, Tag, Typography, message,
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
import ApprovalDiffViewer, { type ApprovalDiffArtifact } from './ApprovalDiffViewer'

const { Paragraph } = Typography

type Props = {
  approvalId: number | null
  open: boolean
  onClose: () => void
  displayTz?: string
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

  const artifacts: ApprovalDiffArtifact[] = preview?.artifacts || []
  const firstChangedKey = useMemo(
    () => artifacts.find(item => item.changed)?.key || artifacts[0]?.key,
    [artifacts],
  )

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
          <Descriptions size="small" column={2} title="调度">
            <Descriptions.Item label="调度类型">{preview.summary?.schedule_type || '—'}</Descriptions.Item>
            <Descriptions.Item label="Cron">{preview.summary?.cron_expression || '—'}</Descriptions.Item>
            <Descriptions.Item label="生命周期">{preview.summary?.status || '—'}</Descriptions.Item>
            <Descriptions.Item label="拓扑">
              {preview.summary?.node_count ?? 0} 节点 / {preview.summary?.edge_count ?? 0} 连线
            </Descriptions.Item>
          </Descriptions>
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
          <Descriptions size="small" column={2} title="API">
            <Descriptions.Item label="编码">{preview.summary?.api_code}</Descriptions.Item>
            <Descriptions.Item label="模式">{preview.summary?.mode}</Descriptions.Item>
            <Descriptions.Item label="当前状态">{preview.summary?.status}</Descriptions.Item>
            <Descriptions.Item label="版本">v{preview.summary?.version ?? 1}</Descriptions.Item>
          </Descriptions>
        )}

        {approval.action === 'offline_api' ? (
          <Alert type="warning" showIcon message="本次审批将 API 下线，请确认调用方迁移情况" />
        ) : null}

        <Alert
          type={preview.has_diff ? 'warning' : 'success'}
          showIcon
          message={preview.has_diff
            ? `${preview.summary?.changed_files ?? 0} 个变更项，+${preview.summary?.additions ?? 0} / -${preview.summary?.deletions ?? 0}`
            : '与对比基准无内容差异'}
          description={preview.snapshot_frozen
            ? `评审内容已冻结；基准：${preview.baseline_label || '首次发布'}`
            : '历史审批单未保存冻结快照，当前展示为兼容预览'}
        />

        {artifacts.length > 0 ? (
          <Tabs
            key={`${approval.id}:${firstChangedKey || ''}`}
            defaultActiveKey={firstChangedKey}
            items={artifacts.map(artifact => ({
              key: artifact.key,
              label: (
                <Space size={4}>
                  <span>{artifact.name}</span>
                  {artifact.changed ? (
                    <>
                      <span style={{ color: '#389e0d' }}>+{artifact.additions}</span>
                      <span style={{ color: '#cf1322' }}>-{artifact.deletions}</span>
                    </>
                  ) : <Tag bordered={false}>未变更</Tag>}
                </Space>
              ),
              children: (
                <ApprovalDiffViewer
                  artifact={artifact}
                  baselineLabel={preview.baseline_label || '空基线'}
                />
              ),
            }))}
          />
        ) : <Alert type="info" showIcon message="该审批没有可预览的内容" />}

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
      width={Math.min(1280, typeof window !== 'undefined' ? window.innerWidth - 48 : 1280)}
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

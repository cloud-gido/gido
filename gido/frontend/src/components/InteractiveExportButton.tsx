/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Button, Dropdown, Space, Tag, Tooltip } from 'antd'
import { DownOutlined, DownloadOutlined, StopOutlined } from '@ant-design/icons'
import type { InteractiveExportFormat, InteractiveRunExport } from '../types/interactiveRun'

export type InteractiveExportDownloadState = 'idle' | 'downloading' | 'downloaded' | 'failed'

const FORMAT_LABELS: Record<InteractiveExportFormat, string> = {
  csv: 'CSV',
  jsonl: 'JSONL',
  xlsx: 'XLSX',
  parquet: 'Parquet',
}

const FORMAT_OPTIONS: InteractiveExportFormat[] = ['csv', 'xlsx', 'jsonl', 'parquet']

export default function InteractiveExportButton({
  format,
  job,
  starting,
  downloadState,
  disabled,
  onExport,
  onCancel,
  onDownload,
}: {
  format: InteractiveExportFormat
  job?: InteractiveRunExport | null
  starting?: boolean
  downloadState: InteractiveExportDownloadState
  disabled?: boolean
  onExport: (format: InteractiveExportFormat) => void
  onCancel: () => void
  onDownload: () => void
}) {
  const running = Boolean(job && ['queued', 'running', 'cancel_requested'].includes(job.status))
  const busy = Boolean(starting || running || downloadState === 'downloading')
  const label = FORMAT_LABELS[format]

  return (
    <Space size={4} wrap>
      <Space.Compact>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          loading={busy}
          disabled={disabled || running}
          onClick={() => onExport(format)}
        >
          {starting ? '正在创建导出…' : running ? `正在生成 ${label}` : `导出 ${label}`}
        </Button>
        <Dropdown
          trigger={['click']}
          disabled={disabled || busy}
          menu={{
            selectedKeys: [format],
            items: FORMAT_OPTIONS.map(item => ({
              key: item,
              label: `导出 ${FORMAT_LABELS[item]}`,
            })),
            onClick: ({ key }) => onExport(key as InteractiveExportFormat),
          }}
        >
          <Button size="small" aria-label="选择导出格式" icon={<DownOutlined />} />
        </Dropdown>
      </Space.Compact>

      {running ? (
        <>
          <Tag color="processing">后台生成中</Tag>
          <Button
            size="small"
            type="link"
            danger
            icon={<StopOutlined />}
            disabled={job?.status === 'cancel_requested'}
            onClick={onCancel}
          >
            取消
          </Button>
        </>
      ) : null}
      {job?.status === 'success' && downloadState === 'downloaded' ? (
        <Tooltip title="文件已自动下载；如未看到浏览器下载，可再次下载">
          <Button size="small" type="link" onClick={onDownload}>再次下载</Button>
        </Tooltip>
      ) : null}
      {job?.status === 'success' && downloadState === 'failed' ? (
        <>
          <Tag color="warning">自动下载未开始</Tag>
          <Button size="small" type="link" onClick={onDownload}>点击下载</Button>
        </>
      ) : null}
      {job?.status === 'failed' ? <Tag color="error">导出失败</Tag> : null}
      {job?.status === 'cancelled' ? <Tag>已取消</Tag> : null}
      {job?.status === 'expired' ? <Tag color="warning">文件已过期</Tag> : null}
    </Space>
  )
}

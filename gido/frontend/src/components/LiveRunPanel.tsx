/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useRef, useState } from 'react'
import { Button, Space, Tag, Typography } from 'antd'
import {
  CopyOutlined,
  DownloadOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  StopOutlined,
} from '@ant-design/icons'
import type { InteractiveRunStatus } from '../hooks/useInteractiveRun'

const STATUS_META: Record<string, { label: string; color: string }> = {
  idle: { label: '未运行', color: 'default' },
  starting: { label: '提交中', color: 'processing' },
  queued: { label: '排队中', color: 'blue' },
  running: { label: '运行中', color: 'processing' },
  cancel_requested: { label: '停止中', color: 'orange' },
  success: { label: '成功', color: 'green' },
  failed: { label: '失败', color: 'red' },
  cancelled: { label: '已停止', color: 'default' },
  timed_out: { label: '已超时', color: 'red' },
}

function formatDuration(value?: number | null): string | null {
  if (value == null) return null
  if (value < 1000) return `${value}ms`
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)}s`
}

export default function LiveRunPanel({
  runId,
  status,
  log,
  error,
  isActive,
  onCancel,
  queueDurationMs,
  executionDurationMs,
  compact = false,
}: {
  runId: number | null
  status: InteractiveRunStatus
  log: string
  error?: string
  isActive: boolean
  onCancel?: () => void | Promise<void>
  queueDurationMs?: number | null
  executionDurationMs?: number | null
  compact?: boolean
}) {
  const boxRef = useRef<HTMLPreElement>(null)
  const [follow, setFollow] = useState(true)
  const meta = STATUS_META[status] || STATUS_META.idle
  const text = log || (isActive ? '等待运行日志…' : error || '暂无日志')
  const queueDuration = formatDuration(queueDurationMs)
  const executionDuration = formatDuration(executionDurationMs)

  useEffect(() => {
    if (follow && boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight
  }, [text, follow])

  const copy = () => navigator.clipboard.writeText(text)
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `gido-run-${runId || 'log'}.log`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div style={{ height: '100%', minHeight: compact ? 180 : 220, display: 'flex', flexDirection: 'column' }}>
      <Space size={8} wrap style={{ padding: '8px 10px', borderBottom: '1px solid var(--ant-color-border-secondary, #f0f0f0)' }}>
        <Tag color={meta.color}>{meta.label}</Tag>
        {runId && <Typography.Text type="secondary">Run #{runId}</Typography.Text>}
        {queueDuration && <Typography.Text type="secondary">排队 {queueDuration}</Typography.Text>}
        {executionDuration && <Typography.Text type="secondary">执行 {executionDuration}</Typography.Text>}
        <Button
          size="small"
          type="text"
          icon={follow ? <PauseOutlined /> : <PlayCircleOutlined />}
          onClick={() => setFollow(value => !value)}
        >
          {follow ? '暂停跟随' : '继续跟随'}
        </Button>
        <Button size="small" type="text" icon={<CopyOutlined />} onClick={copy}>复制</Button>
        <Button size="small" type="text" icon={<DownloadOutlined />} onClick={download}>下载</Button>
        {isActive && onCancel && (
          <Button size="small" danger type="text" icon={<StopOutlined />} onClick={() => void onCancel()}>
            停止
          </Button>
        )}
      </Space>
      <pre
        ref={boxRef}
        style={{
          flex: 1,
          minHeight: 0,
          margin: 0,
          padding: 12,
          overflow: 'auto',
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          fontFamily: 'var(--ant-font-family-code, monospace)',
          fontSize: 12,
          lineHeight: 1.6,
          background: 'var(--ant-color-fill-quaternary, #fafafa)',
        }}
      >
        {text}
        {error && log ? `\n${error}` : ''}
      </pre>
    </div>
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 运行历史列表「语句摘要」：单行首行摘要 + hover 多行 SQL 预览。
 * 与 Studio/Probe 共用等宽字体气质；列表不挂 Monaco，避免表格卡顿。
 */
import type { CSSProperties } from 'react'
import { Tooltip } from 'antd'

const MONO: CSSProperties = {
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
  fontSize: 12.5,
}

type Props = {
  /** 首行摘要（列表主文案） */
  summary?: string | null
  /** hover 多行预览（已截断）；缺省则退回 summary */
  preview?: string | null
  /** 失败且无 SQL 时可用错误摘要 */
  fallback?: string | null
  /** 无 SQL、仅错误时标红 */
  danger?: boolean
}

export default function SqlStatementSummaryCell({
  summary,
  preview,
  fallback,
  danger = false,
}: Props) {
  const line = (summary || '').trim() || (fallback || '').trim()
  if (!line) {
    return <span style={{ color: '#bbb' }}>—</span>
  }
  const hoverBody = (preview || '').trim() || line
  return (
    <Tooltip
      placement="topLeft"
      styles={{
        body: {
          maxWidth: 480,
          maxHeight: 280,
          overflow: 'auto',
          padding: '8px 10px',
        },
      }}
      title={(
        <pre
          style={{
            ...MONO,
            margin: 0,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            color: '#f5f5f5',
            lineHeight: 1.45,
          }}
        >
          {hoverBody}
        </pre>
      )}
    >
      <span
        style={{
          ...MONO,
          color: danger ? '#cf1322' : '#595959',
          display: 'block',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          cursor: 'default',
        }}
      >
        {line}
      </span>
    </Tooltip>
  )
}

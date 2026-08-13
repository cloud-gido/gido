/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React from 'react'
import { FileOutlined } from '@ant-design/icons'
import { resolveLeafTypeBadge } from '../utils/leafTypeBadge'

/**
 * 侧栏叶子：统一白底文档图标 + 淡类型字，避免彩色底块抢视觉。
 * Studio / Probe / Stream 共用，勿再做彩色底块。
 */
export default function LeafTypeBadge({ type }: { type?: string | null }) {
  const meta = resolveLeafTypeBadge(type)
  return (
    <span
      title={meta.title}
      aria-label={meta.title}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        flexShrink: 0,
        marginRight: 6,
        gap: 3,
        verticalAlign: 'middle',
      }}
    >
      <FileOutlined style={{ color: '#bfbfbf', fontSize: 14 }} />
      <span
        style={{
          fontSize: 10,
          lineHeight: 1,
          fontWeight: 400,
          color: '#bfbfbf',
          letterSpacing: 0.2,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        }}
      >
        {meta.label}
      </span>
    </span>
  )
}

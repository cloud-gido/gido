/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React from 'react'
import { resolveLeafTypeBadge } from '../utils/leafTypeBadge'

/**
 * 侧栏叶子类型方标：统一尺寸白底空心描边 + 短缩写，Studio / Probe / Stream 共用。
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
        justifyContent: 'center',
        flexShrink: 0,
        boxSizing: 'border-box',
        width: 18,
        height: 18,
        marginRight: 6,
        borderRadius: 3,
        border: `1px solid ${meta.color}`,
        background: '#fff',
        color: meta.color,
        fontSize: 9,
        fontWeight: 600,
        lineHeight: 1,
        letterSpacing: 0,
        fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif',
        verticalAlign: 'middle',
        userSelect: 'none',
      }}
    >
      {meta.label}
    </span>
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import React from 'react'
import { resolveLeafTypeBadge } from '../utils/leafTypeBadge'

/** 侧栏树叶子类型小标识（Studio / Probe / Stream 共用） */
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
        minWidth: 22,
        height: 14,
        marginRight: 6,
        padding: '0 3px',
        borderRadius: 3,
        fontSize: 9,
        fontWeight: 650,
        lineHeight: 1,
        letterSpacing: 0.15,
        color: meta.fg,
        background: meta.bg,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
        textTransform: 'lowercase',
        verticalAlign: 'middle',
      }}
    >
      {meta.label}
    </span>
  )
}

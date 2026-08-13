/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe / Stream 侧栏叶子类型：统一空心方标 + 短缩写。
 */

export type LeafTypeBadgeMeta = {
  /** 方标内短缩写 */
  label: string
  /** tooltip / 无障碍 */
  title: string
  /** 描边与文字色（同色，白底空心） */
  color: string
}

const BADGE_BY_TYPE: Record<string, LeafTypeBadgeMeta> = {
  SQL: { label: 'Sq', title: 'SQL', color: '#52c41a' },
  JAR: { label: 'Jr', title: 'JAR', color: '#fa8c16' },
  PYTHON: { label: 'Py', title: 'Python', color: '#389e0d' },
  SHELL: { label: 'Sh', title: 'Shell', color: '#d48806' },
  SYNC: { label: 'Sy', title: '数据同步', color: '#722ed1' },
  DEPENDENT: { label: 'Dp', title: '依赖检查', color: '#eb2f96' },
  VIRTUAL: { label: 'Vi', title: '虚拟节点', color: '#8c8c8c' },
}

const FALLBACK: LeafTypeBadgeMeta = {
  label: 'Fl',
  title: '脚本',
  color: '#8c8c8c',
}

/** 归一化 Stream.job_type / Studio.node_type / 显式 leaf_type */
export function normalizeLeafType(
  raw?: string | null,
): string {
  const t = String(raw || '').trim().toUpperCase()
  return t || 'SQL'
}

export function resolveLeafTypeBadge(
  raw?: string | null,
): LeafTypeBadgeMeta {
  const key = normalizeLeafType(raw)
  if (BADGE_BY_TYPE[key]) return BADGE_BY_TYPE[key]
  return {
    ...FALLBACK,
    label: key.slice(0, 2),
    title: key,
  }
}

/** 从叶子行取类型字段（三端字段名不同） */
export function leafTypeFromRow(leaf: {
  leaf_type?: string | null
  job_type?: string | null
  node_type?: string | null
}): string {
  return normalizeLeafType(leaf.leaf_type || leaf.job_type || leaf.node_type)
}

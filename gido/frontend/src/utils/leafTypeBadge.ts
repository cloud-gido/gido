/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe / Stream 侧栏叶子类型提示（统一文档图标 + 淡色类型字）。
 */

export type LeafTypeBadgeMeta = {
  /** 树上显示的短标签 */
  label: string
  /** tooltip / 无障碍 */
  title: string
}

const BADGE_BY_TYPE: Record<string, LeafTypeBadgeMeta> = {
  SQL: { label: 'SQL', title: 'SQL' },
  JAR: { label: 'JAR', title: 'JAR' },
  PYTHON: { label: 'Py', title: 'Python' },
  SHELL: { label: 'Sh', title: 'Shell' },
  SYNC: { label: 'Sync', title: '数据同步' },
  DEPENDENT: { label: 'Dep', title: '依赖检查' },
  VIRTUAL: { label: 'Virt', title: '虚拟节点' },
}

const FALLBACK: LeafTypeBadgeMeta = {
  label: 'File',
  title: '脚本',
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
  return BADGE_BY_TYPE[key] || { ...FALLBACK, label: key.slice(0, 4), title: key }
}

/** 从叶子行取类型字段（三端字段名不同） */
export function leafTypeFromRow(leaf: {
  leaf_type?: string | null
  job_type?: string | null
  node_type?: string | null
}): string {
  return normalizeLeafType(leaf.leaf_type || leaf.job_type || leaf.node_type)
}

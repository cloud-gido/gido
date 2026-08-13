/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe / Stream 侧栏叶子类型小标识（sql / jar / py …）统一映射。
 */

export type LeafTypeBadgeMeta = {
  /** 树上显示的短标签 */
  label: string
  /** tooltip / 无障碍 */
  title: string
  fg: string
  bg: string
}

const BADGE_BY_TYPE: Record<string, LeafTypeBadgeMeta> = {
  SQL: { label: 'sql', title: 'SQL', fg: '#0958d9', bg: '#e6f4ff' },
  JAR: { label: 'jar', title: 'JAR', fg: '#d46b08', bg: '#fff7e6' },
  PYTHON: { label: 'py', title: 'Python', fg: '#389e0d', bg: '#f6ffed' },
  SHELL: { label: 'sh', title: 'Shell', fg: '#d48806', bg: '#fffbe6' },
  SYNC: { label: 'sync', title: '数据同步', fg: '#531dab', bg: '#f9f0ff' },
  DEPENDENT: { label: 'dep', title: '依赖检查', fg: '#c41d7f', bg: '#fff0f6' },
  VIRTUAL: { label: 'virt', title: '虚拟节点', fg: '#595959', bg: '#f5f5f5' },
}

const FALLBACK: LeafTypeBadgeMeta = {
  label: 'file',
  title: '脚本',
  fg: '#595959',
  bg: '#f5f5f5',
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
  return BADGE_BY_TYPE[key] || { ...FALLBACK, label: key.slice(0, 4).toLowerCase(), title: key }
}

/** 从叶子行取类型字段（三端字段名不同） */
export function leafTypeFromRow(leaf: {
  leaf_type?: string | null
  job_type?: string | null
  node_type?: string | null
}): string {
  return normalizeLeafType(leaf.leaf_type || leaf.job_type || leaf.node_type)
}

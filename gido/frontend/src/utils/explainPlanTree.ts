/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

export type ExplainPlanMetrics = {
  cost?: string
  rows?: string
}

/** Extract common EXPLAIN cost/rows tokens from a plan line (PG / Doris / MySQL-ish). */
export function parseExplainPlanMetrics(title: string): ExplainPlanMetrics {
  const text = String(title || '')
  const cost = text.match(/\bcost\s*=\s*([0-9.eE+\-]+(?:\.\.[0-9.eE+\-]+)?)/i)?.[1]
    || text.match(/\bcost\s*[:=]\s*([0-9.eE+\-]+)/i)?.[1]
  const rows = text.match(/\brows\s*=\s*([0-9.eE+\-]+)/i)?.[1]
    || text.match(/\browcount\s*=\s*([0-9.eE+\-]+)/i)?.[1]
  const out: ExplainPlanMetrics = {}
  if (cost) out.cost = cost
  if (rows) out.rows = rows
  return out
}

export type ExplainPlanTreeNode = {
  key: string
  title: string
  metrics?: ExplainPlanMetrics
  children?: ExplainPlanTreeNode[]
}

export function buildExplainPlanTree(snapshot: unknown): ExplainPlanTreeNode[] {
  const rows = snapshot && typeof snapshot === 'object' && Array.isArray((snapshot as any).rows)
    ? (snapshot as any).rows as unknown[]
    : []
  const roots: ExplainPlanTreeNode[] = []
  const stack: Array<{ depth: number; node: ExplainPlanTreeNode }> = []
  rows.forEach((row, index) => {
    const values = Array.isArray(row) ? row : [row]
    const title = values.map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' | ')
    const leading = title.match(/^\s*/)?.[0].length ?? 0
    const depth = title.includes('->') ? Math.max(1, Math.floor(leading / 2) + 1) : 0
    const trimmed = title.trim() || '(空计划行)'
    const metrics = parseExplainPlanMetrics(trimmed)
    const node: ExplainPlanTreeNode = {
      key: String(index),
      title: trimmed,
      ...(Object.keys(metrics).length ? { metrics } : {}),
    }
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop()
    if (stack.length) {
      stack[stack.length - 1].node.children ??= []
      stack[stack.length - 1].node.children!.push(node)
    } else {
      roots.push(node)
    }
    stack.push({ depth, node })
  })
  return roots
}

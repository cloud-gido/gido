/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { classifyColumnType } from './columnTypeBadge'

export type QueryChartField = {
  name: string
  type?: string | null
  semantic_type?: string | null
}

export type QueryChartPoint = {
  category: string
  value: number
}

export function isNumericChartField(field: QueryChartField): boolean {
  const semantic = String(field.semantic_type || '').toLowerCase()
  if (semantic === 'number') return true
  return classifyColumnType(field.type || field.semantic_type || '')?.kind === 'number'
}

export function isCategoryChartField(field: QueryChartField): boolean {
  const semantic = String(field.semantic_type || '').toLowerCase()
  if (semantic === 'number' || semantic === 'boolean' || semantic === 'json' || semantic === 'binary') {
    return false
  }
  const kind = classifyColumnType(field.type || field.semantic_type || '')?.kind
  return kind !== 'number' && kind !== 'boolean' && kind !== 'json'
}

export function suggestQueryChartAxes(fields: QueryChartField[]): {
  category: string | null
  value: string | null
} {
  const category = fields.find(isCategoryChartField)?.name ?? fields[0]?.name ?? null
  const value = fields.find(field => isNumericChartField(field) && field.name !== category)?.name
    ?? fields.find(isNumericChartField)?.name
    ?? null
  return { category, value }
}

export function buildQueryChartPoints(
  rows: Array<Record<string, unknown>>,
  categoryColumn: string,
  valueColumn: string,
  limit = 100,
): QueryChartPoint[] {
  const aggregated = new Map<string, number>()
  for (const row of rows.slice(0, Math.max(1, limit))) {
    const rawCategory = row[categoryColumn]
    const category = rawCategory == null || rawCategory === ''
      ? '(空)'
      : String(rawCategory)
    const rawValue = row[valueColumn]
    const value = typeof rawValue === 'number'
      ? rawValue
      : Number(rawValue)
    if (!Number.isFinite(value)) continue
    aggregated.set(category, (aggregated.get(category) || 0) + value)
  }
  return [...aggregated.entries()]
    .map(([category, value]) => ({ category, value }))
    .sort((a, b) => b.value - a.value)
    .slice(0, limit)
}

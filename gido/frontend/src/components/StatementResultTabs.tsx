/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import type { ReactNode } from 'react'
import { Alert, Tabs } from 'antd'

export type StatementResult = {
  index: number
  sql?: string
  columns: string[]
  column_types?: string[]
  rows?: unknown[][]
  total: number
  truncated?: boolean
  error?: string | null
  status?: string
  affected_rows?: number | null
  started_at?: string | null
  finished_at?: string | null
}

export type MultiStatementRunResult = StatementResult & {
  statement_count?: number
  result_set_count?: number
  statements?: StatementResult[]
}

export function statementResultsOf(result: MultiStatementRunResult | null | undefined): StatementResult[] {
  if (!result) return []
  if (Array.isArray(result.statements) && result.statements.length) return result.statements
  return Array.isArray(result.columns)
    ? [{ ...result, index: Number.isFinite(result.index) ? result.index : 0 }]
    : []
}

export default function StatementResultTabs({
  statements,
  activeKey,
  onChange,
  children,
}: {
  statements: StatementResult[]
  activeKey: string
  onChange: (key: string) => void
  children: (statement: StatementResult | null) => ReactNode
}) {
  const active = statements.find(item => String(item.index) === activeKey) ?? statements[0] ?? null

  return (
    <>
      {statements.length > 1 && (
        <Tabs
          size="small"
          activeKey={active ? String(active.index) : activeKey}
          onChange={onChange}
          style={{ padding: '0 8px', flexShrink: 0 }}
          items={statements.map(item => ({
            key: String(item.index),
            label: item.error || item.status === 'failed'
              ? `语句 ${item.index + 1} ✕`
              : item.status === 'running'
                ? `语句 ${item.index + 1} …`
                : `语句 ${item.index + 1}`,
          }))}
        />
      )}
      {active?.error ? (
        <Alert
          type="error"
          showIcon
          message={`语句 ${active.index + 1} 执行失败`}
          description={active.error}
          style={{ margin: 12 }}
        />
      ) : children(active)}
    </>
  )
}

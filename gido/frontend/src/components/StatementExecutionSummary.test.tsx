/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import StatementExecutionSummary from './StatementExecutionSummary'
import { statementObjectName, statementPresentation } from '../utils/statementPresentation'
import type { InteractiveRunStatement } from '../types/interactiveRun'

afterEach(cleanup)

const base: InteractiveRunStatement = {
  id: 1,
  run_id: 1,
  index: 0,
  columns: [],
  column_types: [],
  total: 0,
  status: 'success',
  result_bytes: 0,
  chunk_count: 0,
  truncated: false,
}

describe('statement presentation', () => {
  it('classifies query, DML, DDL and extracts the changed object', () => {
    expect(statementPresentation({ statement_type: 'SELECT' }).kind).toBe('query')
    expect(statementPresentation({ statement_type: 'INSERT' }).kind).toBe('dml')
    expect(statementPresentation({ statement_type: 'CREATE' }).kind).toBe('ddl')
    expect(statementObjectName('CREATE TABLE IF NOT EXISTS `dw.orders` (id int)')).toBe('dw.orders')
  })

  it('renders affected rows for DML', () => {
    render(
      <StatementExecutionSummary
        statement={{ ...base, statement_type: 'UPDATE', affected_rows: 12 }}
      />,
    )
    expect(screen.getByText('UPDATE 执行成功')).toBeTruthy()
    expect(screen.getByText('影响 12 行')).toBeTruthy()
  })

  it('renders a structure-change summary for DDL', () => {
    render(
      <StatementExecutionSummary
        statement={{
          ...base,
          statement_type: 'ALTER',
          sql: 'ALTER TABLE dw.orders ADD COLUMN note STRING',
        }}
      />,
    )
    expect(screen.getByText('ALTER 执行成功')).toBeTruthy()
    expect(screen.getByText('对象 dw.orders 的结构变更已完成')).toBeTruthy()
  })

  it('uses the statement type in failures', () => {
    render(
      <StatementExecutionSummary
        statement={{
          ...base,
          statement_type: 'DELETE',
          status: 'failed',
          error: 'permission denied',
        }}
      />,
    )
    expect(screen.getByText('DELETE 执行失败')).toBeTruthy()
    expect(screen.getByText('permission denied')).toBeTruthy()
  })
})

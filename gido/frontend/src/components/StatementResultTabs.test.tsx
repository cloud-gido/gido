/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import StatementResultTabs from './StatementResultTabs'
import type { InteractiveRunStatement } from '../types/interactiveRun'

afterEach(cleanup)

function Harness({ statements }: { statements: InteractiveRunStatement[] }) {
  const [active, setActive] = useState(String(statements[0]?.index ?? 0))
  return (
    <StatementResultTabs statements={statements} activeKey={active} onChange={setActive}>
      {statement => <div>active:{statement?.index}</div>}
    </StatementResultTabs>
  )
}

const result = (index: number, error?: string): InteractiveRunStatement => ({
  id: index + 1,
  run_id: 1,
  index,
  statement_type: 'SELECT',
  status: error ? 'failed' : 'success',
  columns: error ? [] : ['id'],
  column_types: error ? [] : ['integer'],
  total: error ? 0 : 1,
  result_bytes: 0,
  chunk_count: 1,
  truncated: false,
  error,
})

describe('StatementResultTabs', () => {
  it('hides the nested tab bar for one result', () => {
    render(<Harness statements={[result(2)]} />)
    expect(screen.getByText('active:2')).toBeTruthy()
    expect(screen.queryByText('结果 3')).toBeNull()
  })

  it('switches multiple statement results and marks errors', () => {
    render(<Harness statements={[result(0), result(2, 'bad sql')]} />)
    expect(screen.getByText('active:0')).toBeTruthy()
    fireEvent.click(screen.getByText('结果 3 ✕'))
    expect(screen.getByText('active:2')).toBeTruthy()
  })
})

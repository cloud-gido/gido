/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import StatementResultTabs, { type StatementResult } from './StatementResultTabs'

afterEach(cleanup)

function Harness({ statements }: { statements: StatementResult[] }) {
  const [active, setActive] = useState(String(statements[0]?.index ?? 0))
  return (
    <StatementResultTabs statements={statements} activeKey={active} onChange={setActive}>
      {statement => <div>active:{statement?.index}</div>}
    </StatementResultTabs>
  )
}

const result = (index: number, error?: string): StatementResult => ({
  index,
  columns: error ? [] : ['id'],
  rows: error ? [] : [[index]],
  total: error ? 0 : 1,
  error,
})

describe('StatementResultTabs', () => {
  it('hides the nested tab bar for one result', () => {
    render(<Harness statements={[result(2)]} />)
    expect(screen.getByText('active:2')).toBeTruthy()
    expect(screen.queryByText('语句 3')).toBeNull()
  })

  it('switches multiple statement results and marks errors', () => {
    render(<Harness statements={[result(0), result(2, 'bad sql')]} />)
    expect(screen.getByText('active:0')).toBeTruthy()
    fireEvent.click(screen.getByText('语句 3 ✕'))
    expect(screen.getByText('语句 3 执行失败')).toBeTruthy()
    expect(screen.getByText('bad sql')).toBeTruthy()
  })
})

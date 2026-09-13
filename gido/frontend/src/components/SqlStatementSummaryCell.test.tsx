/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import SqlStatementSummaryCell from './SqlStatementSummaryCell'

afterEach(cleanup)

describe('SqlStatementSummaryCell', () => {
  it('shows first-line summary and dash when empty', () => {
    const { rerender } = render(<SqlStatementSummaryCell summary="SELECT 1" preview={"SELECT 1\nFROM dual"} />)
    expect(screen.getByText('SELECT 1')).toBeTruthy()
    rerender(<SqlStatementSummaryCell />)
    expect(screen.getByText('—')).toBeTruthy()
  })

  it('falls back to error text when no summary', () => {
    render(<SqlStatementSummaryCell fallback="connection refused" danger />)
    expect(screen.getByText('connection refused')).toBeTruthy()
  })
})

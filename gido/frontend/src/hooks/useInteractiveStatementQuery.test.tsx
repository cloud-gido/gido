/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { adhocRunsApi } from '../api'
import { useInteractiveStatementQuery } from './useInteractiveStatementQuery'

vi.mock('../api', () => ({
  adhocRunsApi: {
    queryStatementRows: vi.fn(),
  },
}))

function page(row: number, nextCursor: string | null) {
  return {
    fields: [{ name: 'id', semantic_type: 'number' }],
    rows: [[row]],
    total: 3,
    source_total: 3,
    next_cursor: nextCursor,
    has_more: Boolean(nextCursor),
    statement_version: 'snapshot-1',
  }
}

describe('useInteractiveStatementQuery page cache', () => {
  beforeEach(() => {
    vi.mocked(adhocRunsApi.queryStatementRows).mockReset()
    vi.mocked(adhocRunsApi.queryStatementRows).mockImplementation(
      async (_runId, _statementIndex, request) => {
        if (request.cursor === 'cursor-2') return page(3, null)
        if (request.cursor === 'cursor-1') return page(2, 'cursor-2')
        return page(1, 'cursor-1')
      },
    )
  })

  it('prefetches the next page and does not reload a previously visited page', async () => {
    const { result } = renderHook(() => useInteractiveStatementQuery({
      runId: 8,
      statementIndex: 0,
      statementVersion: 'snapshot-1',
      limit: 1,
    }))

    await waitFor(() => expect(result.current.data.rows).toEqual([[1]]))
    await waitFor(() => expect(adhocRunsApi.queryStatementRows).toHaveBeenCalledTimes(2))

    act(() => result.current.next())
    await waitFor(() => expect(result.current.data.rows).toEqual([[2]]))
    expect(result.current.loading).toBe(false)

    act(() => result.current.next())
    await waitFor(() => expect(result.current.data.rows).toEqual([[3]]))
    const callsBeforeBack = vi.mocked(adhocRunsApi.queryStatementRows).mock.calls.length

    act(() => result.current.previous())
    await waitFor(() => expect(result.current.data.rows).toEqual([[2]]))
    expect(result.current.loading).toBe(false)
    expect(adhocRunsApi.queryStatementRows).toHaveBeenCalledTimes(callsBeforeBack)
  })
})

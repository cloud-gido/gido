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
      statementStatus: 'success',
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

  it('soft-retries page 1 across progressive version bumps without blanking', async () => {
    vi.mocked(adhocRunsApi.queryStatementRows)
      .mockResolvedValueOnce({
        ...page(1, 'cursor-1'),
        statement_version: 'v1',
        total: 1,
        source_total: 1,
        version_upgraded: false,
      })
      .mockResolvedValueOnce({
        fields: [{ name: 'id', semantic_type: 'number' }],
        rows: [[1], [2]],
        total: 2,
        source_total: 2,
        next_cursor: 'cursor-1',
        has_more: true,
        statement_version: 'v2',
        version_upgraded: true,
      })

    const { result, rerender } = renderHook(
      ({ version, status }) => useInteractiveStatementQuery({
        runId: 8,
        statementIndex: 0,
        statementVersion: version,
        statementStatus: status,
        limit: 10,
      }),
      { initialProps: { version: 'v1', status: 'running' } },
    )

    await waitFor(() => expect(result.current.data.rows).toEqual([[1]]))
    expect(result.current.loading).toBe(false)

    rerender({ version: 'v2', status: 'running' })
    await waitFor(() => expect(result.current.data.rows).toEqual([[1], [2]]))
    expect(result.current.data.statement_version).toBe('v2')
    expect(result.current.loading).toBe(false)
    expect(adhocRunsApi.queryStatementRows).toHaveBeenCalledTimes(2)
  })

  it('resets to page 1 when a cursor page hits a version mismatch', async () => {
    const mismatch = Object.assign(new Error('Conflict'), {
      response: {
        data: {
          detail: {
            code: 'statement_version_mismatch',
            message: '语句版本已变化，请回到第 1 页后重试',
            current_statement_version: 'v2',
          },
        },
      },
    })
    vi.mocked(adhocRunsApi.queryStatementRows)
      .mockResolvedValueOnce({
        ...page(1, 'cursor-1'),
        statement_version: 'v1',
      })
      .mockRejectedValueOnce(mismatch)
      .mockResolvedValueOnce({
        fields: [{ name: 'id', semantic_type: 'number' }],
        rows: [[9]],
        total: 1,
        source_total: 1,
        next_cursor: null,
        has_more: false,
        statement_version: 'v2',
        version_upgraded: true,
      })

    const { result } = renderHook(() => useInteractiveStatementQuery({
      runId: 8,
      statementIndex: 0,
      statementVersion: 'v1',
      // Progressive disables prefetch so the mismatch happens on explicit next().
      statementStatus: 'running',
      limit: 1,
    }))

    await waitFor(() => expect(result.current.data.rows).toEqual([[1]]))

    act(() => result.current.next())
    await waitFor(() => expect(result.current.data.rows).toEqual([[9]]))
    expect(result.current.pageNumber).toBe(1)
    expect(result.current.error).toBe('')
  })

  it('exposes pageCount and jumps to a visited page via goToPage', async () => {
    const { result } = renderHook(() => useInteractiveStatementQuery({
      runId: 8,
      statementIndex: 0,
      statementVersion: 'snapshot-1',
      statementStatus: 'success',
      limit: 1,
    }))

    await waitFor(() => expect(result.current.data.rows).toEqual([[1]]))
    expect(result.current.pageCount).toBe(3)

    act(() => result.current.next())
    await waitFor(() => expect(result.current.data.rows).toEqual([[2]]))
    act(() => result.current.next())
    await waitFor(() => expect(result.current.data.rows).toEqual([[3]]))

    act(() => result.current.goToPage(1))
    await waitFor(() => expect(result.current.data.rows).toEqual([[1]]))
    expect(result.current.pageNumber).toBe(1)

    act(() => result.current.goToPage(3))
    await waitFor(() => expect(result.current.data.rows).toEqual([[3]]))
    expect(result.current.pageNumber).toBe(3)
  })
})

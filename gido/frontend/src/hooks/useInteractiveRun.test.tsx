/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useInteractiveRun } from './useInteractiveRun'
import { adhocRunsApi } from '../api'

vi.mock('../api', () => ({
  adhocRunsApi: {
    events: vi.fn(),
    active: vi.fn(),
    cancel: vi.fn(),
  },
}))

const statement = {
  id: 10,
  run_id: 1,
  index: 0,
  status: 'success',
  columns: ['id'],
  column_types: ['int'],
  total: 1,
  result_bytes: 1,
  chunk_count: 1,
  truncated: false,
}

function event(overrides: Record<string, unknown> = {}) {
  return {
    run_id: 1,
    status: 'success',
    version: 2,
    statement_version: 's1',
    logs: { chunks: [], next_seq: 0, has_more: false },
    statements: [statement],
    statements_changed: true,
    poll_required: false,
    ...overrides,
  }
}

describe('useInteractiveRun', () => {
  beforeEach(() => vi.clearAllMocks())

  it('drains all log pages and never regresses status versions', async () => {
    vi.mocked(adhocRunsApi.events)
      .mockResolvedValueOnce(event({
        logs: { chunks: [{ seq: 1, stream: 'stdout', content: 'A', created_at: '' }], next_seq: 1, has_more: true },
      }) as any)
      .mockResolvedValueOnce(event({
        status: 'running',
        version: 1,
        logs: { chunks: [{ seq: 2, stream: 'stdout', content: 'B', created_at: '' }], next_seq: 2, has_more: false },
        statements: [],
        statements_changed: false,
      }) as any)
    const { result } = renderHook(() => useInteractiveRun({
      source: 'studio',
      workspaceId: 1,
      nodeId: 101,
      autoRecover: false,
    }))

    act(() => result.current.attach(1))
    await waitFor(() => expect(result.current.log).toBe('AB'))
    expect(result.current.status).toBe('success')
    expect(result.current.version).toBe(2)
    expect(result.current.statements).toHaveLength(1)
    expect(adhocRunsApi.events).toHaveBeenCalledTimes(2)
  })

  it('ignores an older start response after a newer start wins', async () => {
    vi.mocked(adhocRunsApi.events).mockResolvedValue(event() as any)
    let resolveFirst!: (value: unknown) => void
    let resolveSecond!: (value: unknown) => void
    const first = new Promise(resolve => { resolveFirst = resolve })
    const second = new Promise(resolve => { resolveSecond = resolve })
    const { result } = renderHook(() => useInteractiveRun({
      source: 'studio',
      workspaceId: 1,
      nodeId: 102,
      autoRecover: false,
    }))

    act(() => {
      void result.current.start(() => first)
      void result.current.start(() => second)
    })
    await act(async () => resolveSecond({ run_id: 2 }))
    await act(async () => resolveFirst({ run_id: 1 }))
    await waitFor(() => expect(result.current.runId).toBe(2))
    expect(adhocRunsApi.events).not.toHaveBeenCalledWith(1, expect.anything())
  })

  it('does not regress a local cancel request on an equal-version poll', async () => {
    let resolveEvents!: (value: unknown) => void
    vi.mocked(adhocRunsApi.events).mockImplementation(() => new Promise(resolve => {
      resolveEvents = resolve
    }) as any)
    vi.mocked(adhocRunsApi.cancel).mockResolvedValue({
      run_id: 3,
      status: 'cancel_requested',
    })
    const { result } = renderHook(() => useInteractiveRun({
      source: 'studio',
      workspaceId: 1,
      nodeId: 103,
      autoRecover: false,
    }))

    act(() => result.current.attach(3))
    await act(async () => {
      await result.current.cancel()
    })
    expect(result.current.status).toBe('cancel_requested')

    await act(async () => resolveEvents(event({
      run_id: 3,
      status: 'running',
      version: -1,
    })))
    expect(result.current.status).toBe('cancel_requested')
  })

  it('aborts attached polling on unmount when recovery is disabled', () => {
    vi.mocked(adhocRunsApi.events).mockImplementation(() => new Promise(() => undefined) as any)
    const { result, unmount } = renderHook(() => useInteractiveRun({
      source: 'studio',
      workspaceId: 1,
      nodeId: 104,
      autoRecover: false,
    }))

    act(() => result.current.attach(4))
    const signal = vi.mocked(adhocRunsApi.events).mock.calls[0][1].signal!
    expect(signal.aborted).toBe(false)
    unmount()
    expect(signal.aborted).toBe(true)
  })
})

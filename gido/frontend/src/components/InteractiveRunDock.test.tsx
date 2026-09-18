/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import type { ReactNode } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import InteractiveRunDock from './InteractiveRunDock'
import { adhocRunsApi } from '../api'

vi.mock('../api', () => ({
  adhocRunsApi: {
    queryStatementRows: vi.fn(),
    explainStatement: vi.fn(),
    createExport: vi.fn(),
    getExport: vi.fn(),
    downloadExport: vi.fn(),
    cancelExport: vi.fn(),
    createShare: vi.fn(),
    revokeShare: vi.fn(),
  },
}))

vi.mock('./QueryResultPanel', () => ({
  default: ({ toolbar, dataSource, onServerChange }: {
    toolbar?: ReactNode
    dataSource: Array<Record<string, unknown>>
    onServerChange?: (change: any) => void
  }) => (
    <div>
      {toolbar}
      <span>rows:{dataSource.map(row => row.id).join(',')}</span>
      <button onClick={() => onServerChange?.({
        filters: { id: ['__contains:2'] },
        sort: { column: 'id', direction: 'desc' },
      })}>
        apply server query
      </button>
    </div>
  ),
}))

afterEach(cleanup)

const run = {
  runId: 8,
  status: 'success' as const,
  version: 3,
  log: 'done',
  error: '',
  statementVersion: 's1',
  startedAt: undefined,
  heartbeatAt: undefined,
  result: null,
  isActive: false,
  statements: [{
    id: 80,
    run_id: 8,
    index: 0,
    statement_version: 's1',
    status: 'success' as const,
    columns: ['id'],
    column_types: ['int'],
    total: 201,
    result_bytes: 3,
    chunk_count: 2,
    truncated: false,
    affected_rows: null,
    started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z',
  }],
  capabilities: {
    hasStatements: true,
    hasResults: true,
    canExport: true,
    supportsMultipleStatements: false,
  },
  start: vi.fn(),
  attach: vi.fn(),
  cancel: vi.fn(),
}

describe('InteractiveRunDock', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:test'),
      revokeObjectURL: vi.fn(),
    })
    vi.mocked(adhocRunsApi.queryStatementRows)
      .mockResolvedValueOnce({
        fields: [{ name: 'id', type: 'int' }],
        rows: [[1], [2]],
        total: 201,
        source_total: 201,
        statement_version: 's1',
        next_cursor: 'next',
        has_more: true,
      })
      .mockResolvedValueOnce({
        fields: [{ name: 'id', type: 'int' }],
        rows: [[3]],
        total: 201,
        source_total: 201,
        statement_version: 's1',
        next_cursor: null,
        has_more: false,
      })
      .mockResolvedValueOnce({
        fields: [{ name: 'id', type: 'int' }],
        rows: [[3], [4]],
        total: 202,
        source_total: 202,
        statement_version: 's2',
        next_cursor: null,
        has_more: false,
      })
  })

  it('renders statement metadata, pages by backend cursor, and completes export', async () => {
    vi.mocked(adhocRunsApi.createExport).mockResolvedValue({
      id: 9,
      run_id: 8,
      statement_id: 80,
      requested_by: 1,
      format: 'csv',
      status: 'success',
      snapshot_scope: 'statement:0',
      row_count: 3,
      size_bytes: 10,
      file_name: 'result.csv',
      created_at: '',
      download_ready: true,
    })
    vi.mocked(adhocRunsApi.downloadExport).mockResolvedValue(new Blob(['id\n1']))
    vi.mocked(adhocRunsApi.explainStatement).mockResolvedValue({
      run_id: 8,
      statement_index: 0,
      plan_snapshot: { scan: 'table' },
      statement: { ...run.statements[0], plan_snapshot: { scan: 'table' } },
    })
    const view = render(<div style={{ height: 500 }}><InteractiveRunDock run={run} scopeKey="test:scope" /></div>)

    await waitFor(() => expect(screen.getByText('rows:1,2')).toBeTruthy())
    expect(screen.getByText('耗时 1.00s')).toBeTruthy()
    fireEvent.click(screen.getByTitle('Next Page').querySelector('button')!)
    await waitFor(() => expect(screen.getByText('rows:3')).toBeTruthy())
    expect(adhocRunsApi.queryStatementRows).toHaveBeenLastCalledWith(
      8,
      0,
      expect.objectContaining({ cursor: 'next', statement_version: 's1' }),
      expect.any(AbortSignal),
    )

    view.rerender(
      <div style={{ height: 500 }}>
        <InteractiveRunDock
          run={{
            ...run,
            statementVersion: 's2',
            statements: [{ ...run.statements[0], statement_version: 's2' }],
          }}
          scopeKey="test:scope"
        />
      </div>,
    )
    await waitFor(() => expect(screen.getByText('rows:3,4')).toBeTruthy())
    expect(adhocRunsApi.queryStatementRows).toHaveBeenLastCalledWith(
      8,
      0,
      expect.objectContaining({ statement_version: 's2' }),
      expect.any(AbortSignal),
    )

    fireEvent.click(screen.getByText('导出 CSV'))
    await waitFor(() => expect(adhocRunsApi.downloadExport).toHaveBeenCalledWith(8, 9))
    expect(screen.getByText('再次下载')).toBeTruthy()
    expect(adhocRunsApi.createExport).toHaveBeenCalledWith(8, {
      statement_index: 0,
      format: 'csv',
      search: undefined,
      filters: undefined,
      sort: undefined,
      statement_version: 's2',
    })
    fireEvent.click(screen.getByRole('button', { name: /导出 CSV/ }))
    await waitFor(() => expect(adhocRunsApi.downloadExport).toHaveBeenCalledTimes(2))
    expect(adhocRunsApi.createExport).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '选择导出格式' }))
    expect(await screen.findByText('导出 XLSX')).toBeTruthy()
    expect(screen.getByText('导出 Parquet')).toBeTruthy()
    expect(screen.getByText('已物化快照')).toBeTruthy()
    fireEvent.click(screen.getByText('再次下载'))
    await waitFor(() => expect(adhocRunsApi.downloadExport).toHaveBeenCalledTimes(3))

    fireEvent.click(screen.getByText('Explain'))
    await waitFor(() => expect(screen.getByText(/"scan": "table"/)).toBeTruthy())
  })

  it('keeps the log tab until the first result page is ready', async () => {
    let resolveRows: ((value: any) => void) | null = null
    vi.mocked(adhocRunsApi.queryStatementRows).mockImplementation(
      () => new Promise(resolve => { resolveRows = resolve }),
    )
    const running = {
      ...run,
      status: 'running' as const,
      isActive: true,
      statements: [{
        ...run.statements[0],
        status: 'running' as const,
        finished_at: null,
      }],
    }

    const view = render(<InteractiveRunDock run={running} scopeKey="test:running" />)

    expect(screen.getByRole('tab', { name: /^日志/ }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tab', { name: /运行结果/ }).getAttribute('aria-selected')).toBe('false')

    resolveRows?.({
      fields: [{ name: 'id', type: 'int' }],
      rows: [[1], [2]],
      total: 201,
      source_total: 201,
      statement_version: 's1',
      next_cursor: 'next',
      has_more: true,
    })
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /运行结果/ }).getAttribute('aria-selected')).toBe('true')
    })
    fireEvent.click(screen.getByRole('tab', { name: /^日志/ }))
    view.rerender(<InteractiveRunDock run={{ ...run, status: 'success' }} scopeKey="test:running" />)
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: /^日志/ }).getAttribute('aria-selected')).toBe('true')
    })
  })

  it('offers an explicit retry when automatic download fails', async () => {
    vi.mocked(adhocRunsApi.createExport).mockResolvedValue({
      id: 10,
      run_id: 8,
      statement_id: 80,
      requested_by: 1,
      format: 'csv',
      status: 'success',
      snapshot_scope: 'statement:0',
      row_count: 3,
      size_bytes: 10,
      file_name: 'result.csv',
      created_at: '',
      download_ready: true,
    })
    vi.mocked(adhocRunsApi.downloadExport)
      .mockRejectedValueOnce(new Error('browser blocked'))
      .mockResolvedValueOnce(new Blob(['id\n1']))

    render(<InteractiveRunDock run={run} scopeKey="test:export-retry" />)
    await waitFor(() => expect(screen.getByText('rows:1,2')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /导出 CSV/ }))
    expect(await screen.findByText('点击下载')).toBeTruthy()
    fireEvent.click(screen.getByText('点击下载'))
    await waitFor(() => expect(adhocRunsApi.downloadExport).toHaveBeenCalledTimes(2))
    expect(screen.getByText('再次下载')).toBeTruthy()
  })

  it('creates, copies, and revokes a workspace share from the shared dock', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
    vi.mocked(adhocRunsApi.createShare).mockResolvedValue({
      id: 12,
      run_id: 8,
      created_by: 1,
      expires_at: '2026-01-02T00:00:00Z',
      created_at: '2026-01-01T00:00:00Z',
      active: true,
      token: 'share-token',
    })
    vi.mocked(adhocRunsApi.revokeShare).mockResolvedValue({
      id: 12,
      run_id: 8,
      created_by: 1,
      expires_at: '2026-01-02T00:00:00Z',
      revoked_at: '2026-01-01T01:00:00Z',
      created_at: '2026-01-01T00:00:00Z',
      active: false,
    })

    render(<InteractiveRunDock run={run} scopeKey="test:share" />)
    fireEvent.click(screen.getByRole('button', { name: /空间内分享/ }))
    await waitFor(() => expect(screen.getByText('复制链接')).toBeTruthy())
    expect(adhocRunsApi.createShare).toHaveBeenCalledWith(8, 24)
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      expect.stringContaining('/gido/share/run/share-token'),
    )
    const revokeButton = screen.getByRole('button', { name: /撤\s*销/ })
    fireEvent.click(revokeButton)
    await waitFor(() => expect(adhocRunsApi.revokeShare).toHaveBeenCalledWith(8, 12))
  })
})

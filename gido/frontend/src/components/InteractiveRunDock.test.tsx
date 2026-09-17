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
    statementRows: vi.fn(),
    createExport: vi.fn(),
    getExport: vi.fn(),
    downloadExport: vi.fn(),
  },
}))

vi.mock('./QueryResultPanel', () => ({
  default: ({ toolbar, dataSource }: { toolbar?: ReactNode; dataSource: Array<Record<string, unknown>> }) => (
    <div>{toolbar}<span>rows:{dataSource.map(row => row.id).join(',')}</span></div>
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
    vi.clearAllMocks()
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:test'),
      revokeObjectURL: vi.fn(),
    })
    vi.mocked(adhocRunsApi.statementRows)
      .mockResolvedValueOnce({
        run_id: 8,
        run_status: 'success',
        run_version: 3,
        statement_id: 80,
        statement_index: 0,
        statement_status: 'success',
        snapshot: true,
        columns: ['id'],
        column_types: ['int'],
        rows: [[1], [2]],
        total: 201,
        truncated: false,
        next_cursor: 'next',
        has_more: true,
      })
      .mockResolvedValueOnce({
        run_id: 8,
        run_status: 'success',
        run_version: 3,
        statement_id: 80,
        statement_index: 0,
        statement_status: 'success',
        snapshot: true,
        columns: ['id'],
        column_types: ['int'],
        rows: [[3]],
        total: 201,
        truncated: false,
        next_cursor: null,
        has_more: false,
      })
      .mockResolvedValueOnce({
        run_id: 8,
        run_status: 'success',
        run_version: 3,
        statement_id: 80,
        statement_index: 0,
        statement_status: 'success',
        snapshot: true,
        columns: ['id'],
        column_types: ['int'],
        rows: [[3], [4]],
        total: 202,
        truncated: false,
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
    const view = render(<div style={{ height: 500 }}><InteractiveRunDock run={run} scopeKey="test:scope" /></div>)

    await waitFor(() => expect(screen.getByText('rows:1,2')).toBeTruthy())
    expect(screen.getByText('耗时 1.00s')).toBeTruthy()
    fireEvent.click(screen.getByTitle('Next Page').querySelector('button')!)
    await waitFor(() => expect(screen.getByText('rows:3')).toBeTruthy())
    expect(adhocRunsApi.statementRows).toHaveBeenLastCalledWith(8, 0, expect.objectContaining({ cursor: 'next' }))

    view.rerender(
      <div style={{ height: 500 }}>
        <InteractiveRunDock run={{ ...run, statementVersion: 's2' }} scopeKey="test:scope" />
      </div>,
    )
    await waitFor(() => expect(screen.getByText('rows:3,4')).toBeTruthy())
    expect(adhocRunsApi.statementRows).toHaveBeenLastCalledWith(8, 0, expect.objectContaining({ cursor: 'next' }))

    fireEvent.click(screen.getByText('导出 CSV'))
    await waitFor(() => expect(screen.getByText('下载')).toBeTruthy())
    fireEvent.click(screen.getByText('下载'))
    await waitFor(() => expect(adhocRunsApi.downloadExport).toHaveBeenCalledWith(8, 9))
  })
})

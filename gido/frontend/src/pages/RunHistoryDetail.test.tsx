/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

vi.mock('../api', () => ({
  adhocRunsApi: {
    get: vi.fn(),
  },
}))

vi.mock('../components/DwMonacoEditor', () => ({
  default: ({ value, readOnly }: { value?: string; readOnly?: boolean }) => (
    <div data-testid="dw-monaco" data-readonly={readOnly ? '1' : '0'}>{value}</div>
  ),
}))

vi.mock('../components/QueryResultPanel', () => ({
  default: () => <div data-testid="query-result-panel" />,
}))

vi.mock('../store', () => ({
  useAppStore: () => ({ currentWorkspace: { id: 1, timezone: 'Asia/Shanghai' } }),
}))

import { adhocRunsApi } from '../api'
import RunHistoryDetailPage from '../pages/RunHistoryDetail'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('RunHistoryDetail read-only SQL', () => {
  beforeEach(() => {
    ;(adhocRunsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 7387,
      status: 'success',
      source: 'studio',
      sql_text: 'SHOW CREATE CATALOG paimon;',
      object_name: 'demo',
      result_preview: { columns: ['Catalog'], rows: [['paimon']], truncated: false },
    })
  })

  it('renders DwMonacoEditor read-only for 执行语句', async () => {
    render(
      <MemoryRouter initialEntries={['/batch/run-history/7387']}>
        <Routes>
          <Route path="/batch/run-history/:id" element={<RunHistoryDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByTestId('dw-monaco')).toBeTruthy())
    const ed = screen.getByTestId('dw-monaco')
    expect(ed.getAttribute('data-readonly')).toBe('1')
    expect(ed.textContent).toContain('SHOW CREATE CATALOG paimon;')
  })
})

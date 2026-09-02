/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import ApprovalResourcePreviewDrawer from './ApprovalResourcePreviewDrawer'
import { approvalApi } from '../api'
import { R } from '../routes'

vi.mock('@monaco-editor/react', () => ({
  default: ({ value }: { value: string }) => <div data-testid="monaco-script">{value}</div>,
}))

vi.mock('../api', () => ({
  approvalApi: {
    preview: vi.fn(),
  },
}))

const previewMock = vi.mocked(approvalApi.preview)

const samplePayload = {
  approval: {
    id: 42,
    resource_type: 'studio_node',
    resource_id: 9,
    resource_name: 'ads_demo',
    action: 'publish_node',
    submit_note: '请审批',
    submitted_by_username: 'dev',
    submitted_at: '2026-09-02T03:00:00Z',
  },
  preview: {
    kind: 'studio_node',
    action: 'publish_node',
    summary: { node_type: 'SQL', is_published: false },
    pending: { script_content: 'SELECT 42' },
    baseline: { script_content: 'SELECT 1' },
    baseline_label: '最近保存版本',
    has_diff: true,
  },
}

function renderDrawer(open = true) {
  return render(
    <MemoryRouter>
      <ApprovalResourcePreviewDrawer
        approvalId={42}
        open={open}
        onClose={() => {}}
        displayTz="Asia/Shanghai"
      />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  previewMock.mockReset()
})

describe('ApprovalResourcePreviewDrawer', () => {
  it('loads preview and renders script + diff tabs', async () => {
    previewMock.mockResolvedValue(samplePayload as any)
    renderDrawer()

    await waitFor(() => {
      expect(previewMock).toHaveBeenCalledWith(42)
    })

    expect(await screen.findByText('审批预览 — ads_demo')).toBeInTheDocument()
    expect(screen.getByText('请审批')).toBeInTheDocument()
    expect(screen.getByText('本次提交')).toBeInTheDocument()
    expect(screen.getByText('最近保存版本')).toBeInTheDocument()
    expect(screen.getByTestId('monaco-script')).toHaveTextContent('SELECT 42')
  })

  it('shows deep link to studio when resource is studio_node', async () => {
    previewMock.mockResolvedValue(samplePayload as any)
    renderDrawer()

    const links = await screen.findAllByRole('link', { name: /在数据开发中打开/ })
    expect(links.length).toBeGreaterThanOrEqual(1)
    expect(links[0]).toHaveAttribute('href', `${R.batch.studio}?node_id=9`)
    expect(links[0]).toHaveAttribute('target', '_blank')
  })

  it('does not fetch when drawer closed', async () => {
    renderDrawer(false)
    await new Promise(r => setTimeout(r, 50))
    expect(previewMock).not.toHaveBeenCalled()
  })
})

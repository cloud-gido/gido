/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * DAGEditor 组件集成：全屏后脚本 Select 仍可用，且浮层 zIndex 高于全屏壳。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Z_FULLSCREEN, Z_POPUP } from './dagEditorOverlay'

const graphApi = {
  on: vi.fn(),
  dispose: vi.fn(),
  resize: vi.fn(),
  getNodes: vi.fn(() => []),
  getEdges: vi.fn(() => []),
  getCellById: vi.fn(() => null),
  clearCells: vi.fn(),
  addNode: vi.fn(),
  addEdge: vi.fn(),
  removeCell: vi.fn(),
  batchUpdate: vi.fn((fn: () => void) => fn()),
  zoomToFit: vi.fn(),
}

vi.mock('@antv/x6', () => ({
  Graph: vi.fn(function Graph() {
    return graphApi
  }),
  Shape: {
    Edge: vi.fn(),
  },
}))

import DAGEditor from './DAGEditor'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

beforeEach(() => {
  Object.values(graphApi).forEach((fn) => {
    if (typeof fn === 'function' && 'mockClear' in fn) (fn as any).mockClear?.()
  })
  graphApi.getNodes.mockReturnValue([])
  graphApi.getEdges.mockReturnValue([])
  graphApi.getCellById.mockReturnValue(null)
})

const published = [
  { id: 11, name: 'import_dim_goods_daily', node_type: 'SQL', is_published: true },
  { id: 12, name: 'ads_order_summary', node_type: 'SQL', is_published: true },
  { id: 13, name: 'draft_only_script', node_type: 'SQL', is_published: false },
]

describe('DAGEditor fullscreen script select', () => {
  it('lists only published scripts and shows options after entering fullscreen', async () => {
    const user = userEvent.setup()
    render(
      <DAGEditor
        nodes={published}
        value={{ nodes: [], edges: [] }}
      />,
    )

    const combo = screen.getByRole('combobox')
    expect(combo).toBeEnabled()

    await user.click(screen.getByRole('button', { name: /全屏/ }))
    expect(screen.getByRole('button', { name: /退出全屏/ })).toBeInTheDocument()

    // 全屏壳 fixed + zIndex
    let el: HTMLElement | null = screen.getByRole('button', { name: /退出全屏/ }).parentElement
    let foundFs = false
    while (el) {
      if (el.style?.position === 'fixed' && el.style?.zIndex === String(Z_FULLSCREEN)) {
        foundFs = true
        break
      }
      el = el.parentElement
    }
    expect(foundFs).toBe(true)

    await user.click(screen.getByRole('combobox'))
    // rc-select 另有隐藏 listbox（只含 value）；可视选项在 .ant-select-item
    const goods = await screen.findByText('import_dim_goods_daily')
    expect(goods).toBeInTheDocument()
    expect(screen.getByText('ads_order_summary')).toBeInTheDocument()
    expect(screen.queryByText('draft_only_script')).toBeNull()

    const dropdown = document.querySelector('.ant-select-dropdown') as HTMLElement | null
    expect(dropdown).not.toBeNull()
    const z = Number.parseInt(String(dropdown!.style.zIndex || getComputedStyle(dropdown!).zIndex), 10)
    expect(z).toBeGreaterThanOrEqual(Z_POPUP)
    expect(z).toBeGreaterThan(Z_FULLSCREEN)
  })

  it('disables add select when no published scripts', () => {
    render(
      <DAGEditor
        nodes={[{ id: 1, name: 'draft', node_type: 'SQL', is_published: false }]}
        value={{ nodes: [], edges: [] }}
      />,
    )
    expect(screen.getByRole('combobox')).toBeDisabled()
    expect(screen.getByText('暂无已提交脚本可添加')).toBeInTheDocument()
  })
})

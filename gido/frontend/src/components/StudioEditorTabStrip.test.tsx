/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * UI 级 E2E：Tab 铬态、激活、关闭会话提示（jsdom + Testing Library）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StudioEditorTabStrip from './StudioEditorTabStrip'

afterEach(() => {
  cleanup()
})

const tabs = [
  { id: 1, name: 'ads_active', node_type: 'SQL', script_content: 'SELECT 1' },
  { id: 2, name: 'dws_pending', node_type: 'SQL', script_content: null },
  { id: 3, name: 'dim_error', node_type: 'SQL', script_content: null },
]

describe('StudioEditorTabStrip E2E chrome', () => {
  it('renders all tabs at once with pending italic and ready normal', () => {
    render(
      <StudioEditorTabStrip
        tabs={tabs}
        activeTabId={1}
        dirtyMap={{}}
        tabContentLoading={{}}
        tabContentError={{}}
        onActivate={() => {}}
        onClose={() => {}}
        onReload={() => {}}
        onCloseOthers={() => {}}
        onCloseLeft={() => {}}
        onCloseRight={() => {}}
        onCloseAll={() => {}}
      />,
    )
    expect(screen.getByTestId('studio-tab-1')).toHaveAttribute('data-chrome', 'ready')
    expect(screen.getByTestId('studio-tab-2')).toHaveAttribute('data-chrome', 'pending')
    expect(screen.getByTestId('studio-tab-title-2')).toHaveStyle({ fontStyle: 'italic' })
    expect(screen.getByTestId('studio-tab-title-1')).toHaveStyle({ fontStyle: 'normal' })
  })

  it('shows error marker and activates on click', async () => {
    const user = userEvent.setup()
    const onActivate = vi.fn()
    render(
      <StudioEditorTabStrip
        tabs={tabs}
        activeTabId={1}
        dirtyMap={{}}
        tabContentLoading={{}}
        tabContentError={{ 3: '网络错误' }}
        onActivate={onActivate}
        onClose={() => {}}
        onReload={() => {}}
        onCloseOthers={() => {}}
        onCloseLeft={() => {}}
        onCloseRight={() => {}}
        onCloseAll={() => {}}
      />,
    )
    expect(screen.getByTestId('studio-tab-3')).toHaveAttribute('data-chrome', 'error')
    expect(screen.getByTestId('studio-tab-error-3')).toBeInTheDocument()
    await user.click(screen.getByTestId('studio-tab-3'))
    expect(onActivate).toHaveBeenCalledWith(3)
  })

  it('context menu exposes reload and session close hint', async () => {
    const user = userEvent.setup()
    const onReload = vi.fn()
    render(
      <StudioEditorTabStrip
        tabs={tabs}
        activeTabId={1}
        dirtyMap={{}}
        tabContentLoading={{}}
        tabContentError={{ 2: '超时' }}
        onActivate={() => {}}
        onClose={() => {}}
        onReload={onReload}
        onCloseOthers={() => {}}
        onCloseLeft={() => {}}
        onCloseRight={() => {}}
        onCloseAll={() => {}}
      />,
    )
    await user.pointer({ keys: '[MouseRight>]', target: screen.getByTestId('studio-tab-2') })
    const menu = await screen.findByRole('menu')
    expect(within(menu).getByText('重新加载脚本')).toBeInTheDocument()
    expect(within(menu).getByText(/关闭即移出会话/)).toBeInTheDocument()
    await user.click(within(menu).getByText('重新加载脚本'))
    expect(onReload).toHaveBeenCalledWith(2)
  })
})

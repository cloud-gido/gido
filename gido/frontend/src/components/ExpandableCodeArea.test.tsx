/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ExpandableCodeArea from './ExpandableCodeArea'

afterEach(() => {
  cleanup()
})

describe('ExpandableCodeArea', () => {
  it('enters and exits fullscreen like workflow DAG', async () => {
    const user = userEvent.setup()
    render(<ExpandableCodeArea value="SELECT 1" title="SQL 模板" />)

    expect(screen.getByTestId('expandable-code-area')).toBeInTheDocument()
    expect(screen.queryByTestId('expandable-code-area-fullscreen')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '全屏' }))
    expect(screen.getByTestId('expandable-code-area-fullscreen')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '退出全屏' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '退出全屏' }))
    expect(screen.queryByTestId('expandable-code-area-fullscreen')).not.toBeInTheDocument()
  })

  it('exits fullscreen on Escape', async () => {
    const user = userEvent.setup()
    render(<ExpandableCodeArea value="SELECT 1" />)
    await user.click(screen.getByRole('button', { name: '全屏' }))
    expect(screen.getByTestId('expandable-code-area-fullscreen')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByTestId('expandable-code-area-fullscreen')).not.toBeInTheDocument()
  })
})

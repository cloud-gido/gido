/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StudioWorkbenchShell, {
  STUDIO_WORKBENCH_BLEED_STYLE,
  StudioWorkbenchEmpty,
  StudioWorkbenchExpandSidebarButton,
  StudioWorkbenchStage,
  StudioWorkbenchToolbar,
  StudioWorkbenchTopStrip,
} from './StudioWorkbenchShell'

afterEach(() => {
  cleanup()
})

describe('StudioWorkbenchShell', () => {
  it('uses shared bleed style for full-height workbench', () => {
    expect(STUDIO_WORKBENCH_BLEED_STYLE).toMatchObject({
      height: 'calc(100vh - 112px)',
      margin: -24,
      overflow: 'hidden',
    })
  })

  it('renders sidebar title, tree, actions and right-stage children', () => {
    render(
      <StudioWorkbenchShell
        storageKey="gido.test.workbench.sidebarWidth"
        collapsed={false}
        sidebarTitle="作业列表"
        sidebarClassName="shell-left"
        treeBodyClassName="shell-tree"
        sidebarActions={<button type="button">新建</button>}
        tree={<div>tree-leaf</div>}
      >
        <div>editor-stage</div>
      </StudioWorkbenchShell>,
    )

    expect(screen.getByText('作业列表')).toBeInTheDocument()
    expect(screen.getByText('tree-leaf')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建' })).toBeInTheDocument()
    expect(screen.getByText('editor-stage')).toBeInTheDocument()
    expect(document.querySelector('.shell-left')).not.toBeNull()
    expect(document.querySelector('.shell-tree')).not.toBeNull()
  })

  it('collapses left pane while keeping right children', () => {
    const { rerender } = render(
      <StudioWorkbenchShell
        storageKey="gido.test.workbench.sidebarWidth.collapsed"
        collapsed={false}
        sidebarTitle="节点列表"
        tree={<div>nodes</div>}
      >
        <div>right</div>
      </StudioWorkbenchShell>,
    )
    expect(screen.getByText('nodes')).toBeVisible()

    rerender(
      <StudioWorkbenchShell
        storageKey="gido.test.workbench.sidebarWidth.collapsed"
        collapsed
        sidebarTitle="节点列表"
        tree={<div>nodes</div>}
      >
        <div>right</div>
      </StudioWorkbenchShell>,
    )
    expect(screen.getByText('right')).toBeInTheDocument()
    expect(document.querySelector('[aria-hidden="true"]')).not.toBeNull()
  })
})

describe('StudioWorkbench chrome slots', () => {
  it('TopStrip applies padded spacing when requested', () => {
    const { container } = render(
      <StudioWorkbenchTopStrip padded>
        <span>title</span>
      </StudioWorkbenchTopStrip>,
    )
    const strip = container.firstElementChild as HTMLElement
    expect(strip.style.padding).toBe('0px 12px')
    expect(strip.style.gap).toBe('8px')
    expect(screen.getByText('title')).toBeInTheDocument()
  })

  it('Toolbar can wrap; Stage and Empty keep flex fill layout', () => {
    const { container: toolbarRoot } = render(
      <StudioWorkbenchToolbar wrap>
        <button type="button">保存</button>
      </StudioWorkbenchToolbar>,
    )
    expect((toolbarRoot.firstElementChild as HTMLElement).style.flexWrap).toBe('wrap')

    const { container: stageRoot } = render(
      <StudioWorkbenchStage>
        <div>monaco</div>
      </StudioWorkbenchStage>,
    )
    expect((stageRoot.firstElementChild as HTMLElement).style.flex).toBe('1 1 0%')

    render(
      <StudioWorkbenchEmpty>
        <span>请选择作业</span>
      </StudioWorkbenchEmpty>,
    )
    expect(screen.getByText('请选择作业')).toBeInTheDocument()
  })

  it('ExpandSidebarButton renders only when collapsed and invokes onExpand', async () => {
    const user = userEvent.setup()
    const onExpand = vi.fn()
    const { rerender, container } = render(
      <StudioWorkbenchExpandSidebarButton
        collapsed={false}
        onExpand={onExpand}
        tooltip="显示作业列表"
      />,
    )
    expect(within(container).queryByRole('button')).toBeNull()

    rerender(
      <StudioWorkbenchExpandSidebarButton
        collapsed
        onExpand={onExpand}
        tooltip="显示作业列表"
      />,
    )
    await user.click(within(container).getByRole('button'))
    expect(onExpand).toHaveBeenCalledTimes(1)
  })
})

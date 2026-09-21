/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Guards the user-visible scroll contract: every page size uses the same native
 * scroll owner (`.dw-query-result__main`), never antd virtual / fake tracks.
 */
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ColumnsType } from 'antd/es/table'
import QueryResultPanel from './QueryResultPanel'
import type { QueryRowRec } from './QueryResultTable'

afterEach(cleanup)

function buildColumns(): ColumnsType<QueryRowRec> {
  return [
    { title: 'id', dataIndex: 'id', key: 'id', width: 120 },
    { title: 'name', dataIndex: 'name', key: 'name', width: 240 },
    { title: 'zone', dataIndex: 'zone', key: 'zone', width: 180 },
    { title: 'metric_a', dataIndex: 'metric_a', key: 'metric_a', width: 160 },
    { title: 'metric_b', dataIndex: 'metric_b', key: 'metric_b', width: 160 },
    { title: 'note', dataIndex: 'note', key: 'note', width: 320 },
  ]
}

function buildRows(count: number): QueryRowRec[] {
  return Array.from({ length: count }, (_, index) => ({
    _key: index,
    id: index + 1,
    name: `row-${index + 1}`,
    zone: index % 2 === 0 ? 'MULTI' : 'SINGLE',
    metric_a: index * 10,
    metric_b: index * 0.5,
    note: `padding-${'x'.repeat(24)}-${index}`,
  }))
}

function renderPanel(rowCount: number) {
  const view = render(
    <div style={{ width: 480, height: 260, display: 'flex', flexDirection: 'column' }}>
      <QueryResultPanel
        columns={buildColumns()}
        dataSource={buildRows(rowCount)}
        pagination={false}
      />
    </div>,
  )
  const main = view.container.querySelector('.dw-query-result__main') as HTMLElement | null
  expect(main).toBeTruthy()
  return { ...view, main: main! }
}

/** jsdom has no real layout; assert the intended scroll owner still accepts scrollTop. */
function assertScrollOwnerAcceptsDrag(main: HTMLElement) {
  expect(main.className).toContain('dw-query-result__main')
  expect(main.className).not.toContain('dw-query-result__main--virtual')
  expect(main.querySelector('.rc-virtual-list-holder')).toBeNull()
  expect(main.querySelector('.rc-virtual-list')).toBeNull()
  expect(document.querySelector('.dw-query-result__vscroll')).toBeNull()
  expect(document.querySelector('.dw-query-result__hscroll')).toBeNull()

  main.style.height = '120px'
  main.style.width = '240px'
  main.style.overflow = 'auto'
  main.scrollTop = 0
  main.scrollLeft = 0
  main.scrollTop = 48
  main.scrollLeft = 36
  expect(main.scrollTop).toBe(48)
  expect(main.scrollLeft).toBe(36)
}

describe('QueryResultPanel scroll + selection contract', () => {
  it('keeps the same native scroll owner for 100-row and 200-row pages', () => {
    const small = renderPanel(100)
    assertScrollOwnerAcceptsDrag(small.main)
    expect(within(small.main).getAllByRole('button').length).toBeGreaterThanOrEqual(100)
    cleanup()

    const large = renderPanel(200)
    assertScrollOwnerAcceptsDrag(large.main)
    expect(within(large.main).getAllByRole('button').length).toBeGreaterThanOrEqual(200)
    expect(large.main.classList.contains('dw-query-result__main')).toBe(true)
    expect(large.main.classList.contains('dw-query-result__main--virtual')).toBe(false)
  })

  it('selects a full row via row-number click on a tall page', () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
    const { main } = renderPanel(200)
    const rowButtons = within(main).getAllByRole('button')
    // First body row number (header icon button may come first).
    const rowOne = rowButtons.find(btn => btn.textContent?.trim() === '1')
    expect(rowOne).toBeTruthy()
    fireEvent.click(rowOne!)
    const selected = main.querySelectorAll('.dw-query-result__cell--selected')
    expect(selected.length).toBeGreaterThanOrEqual(6)
  })

  it('selects the whole page with ⌘/Ctrl+A after focusing the grid', () => {
    const { main } = renderPanel(120)
    const cell = main.querySelector('[data-qr-row="0"][data-qr-col="0"]') as HTMLElement | null
    expect(cell).toBeTruthy()
    fireEvent.mouseDown(cell!)
    fireEvent.keyDown(cell!, { key: 'a', metaKey: true })
    const selected = main.querySelectorAll('.dw-query-result__cell--selected')
    // 120 rows × 6 data columns
    expect(selected.length).toBe(120 * 6)
    expect(screen.getByRole('status').textContent || '').toMatch(/120/)
  })
})

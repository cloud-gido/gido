/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * GIDO 主列表：统一可拖拽列宽 + 拖拽不触发排序（共享 hook / gesture）。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

const LIST_PAGES: Array<{ file: string; storageHint: string }> = [
  { file: 'pages/Operation.tsx', storageHint: 'gido.ops.instances.cols' },
  { file: 'pages/AlertCenter.tsx', storageHint: 'gido.alert' },
  { file: 'pages/Integration.tsx', storageHint: 'gido.integration.tasks.cols' },
  { file: 'pages/Workflow.tsx', storageHint: 'gido.workflow.tableCols' },
  { file: 'pages/RunHistory.tsx', storageHint: 'gido.batch.runHistory.cols' },
]


describe('resizable list adoption', () => {
  it('shared hook suppresses sorter clicks after resize', () => {
    const hook = read('hooks/useResizableTableColumns.tsx')
    expect(hook).toContain('shouldSuppressHeaderInteraction')
    expect(hook).toContain('ResizableColumnTitle')
    const title = read('components/ResizableColumnTitle.tsx')
    expect(title).toContain('markColumnResizeGesture')
    expect(title).toContain('stopPropagation')
    const gesture = read('utils/columnResizeGesture.ts')
    expect(gesture).toContain('markColumnResizeGesture')
  })

  it.each(LIST_PAGES)('$file adopts useResizableTableColumns', ({ file, storageHint }) => {
    const src = read(file)
    expect(src).toContain('useResizableTableColumns')
    expect(src).toContain('dw-resizable-table')
    expect(src).toContain(storageHint)
  })

  it('query result panel ignores sort while resize gesture is active', () => {
    const panel = read('components/QueryResultPanel.tsx')
    expect(panel).toContain('shouldSuppressHeaderInteraction')
    const table = read('components/QueryResultTable.tsx')
    expect(table).toContain('markColumnResizeGesture')
  })

  it('query result panel scrolls the table viewport itself', () => {
    const panel = read('components/QueryResultPanel.tsx')
    const css = read('components/queryResultPanel.css')
    // One scroll owner: `.dw-query-result__main`. No fake outer tracks, no antd virtual.
    expect(panel).not.toContain('dw-query-result__vscroll')
    expect(panel).not.toContain('dw-query-result__hscroll')
    expect(panel).not.toContain('virtual={')
    expect(css).not.toContain('dw-query-result__main--virtual')
    expect(css).not.toContain('rc-virtual-list')
    expect(css).not.toContain('dw-query-result__vscroll')
    expect(css).toContain('.dw-query-result__main')
    expect(css).toContain('overflow: auto')
  })

  it('query result panel keeps row/column select and focus-column stats', () => {
    const panel = read('components/QueryResultPanel.tsx')
    expect(panel).toContain('selectQueryResultRow')
    expect(panel).toContain('selectQueryResultColumn')
    expect(panel).toContain('selectQueryResultAll')
    expect(panel).toContain('computeQueryColumnPageStats')
  })

  it('query result grid adopts fit-width and rectangular selection', () => {
    const table = read('components/QueryResultTable.tsx')
    const panel = read('components/QueryResultPanel.tsx')
    const css = read('components/queryResultPanel.css')
    expect(table).toContain('measureQueryColumnWidth')
    expect(table).toContain('双击按内容自适应')
    expect(panel).toContain('formatQueryResultSelectionTsv')
    expect(panel).toContain('dw-query-result__cell--selected')
    expect(panel).toContain('复制选区')
    expect(panel).toContain('moveQueryResultFocus')
    expect(panel).toContain('fitQueryColumnsToViewport')
    expect(panel).toContain('fitQueryColumnsToContent')
    expect(panel).toContain('dw-query-result__aggregate')
    expect(panel).toContain('适合窗口')
    expect(panel).toContain('展开表头')
    expect(panel).toContain('dw-query-result__fit-actions')
    expect(panel).not.toContain('Dropdown.Button')
    expect(panel).toContain('showQuickJumper')
    expect(panel).toContain('findQueryResultMatches')
    expect(panel).toContain('dw-query-result__find')
    expect(panel).toContain('dw-query-result__cell--focus')
    expect(css).toContain('content-visibility: auto')
  })
})

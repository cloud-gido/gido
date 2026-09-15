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
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归契约：作业运维默认「关注中」视图 + 可拖拽表头（与批工作流同一 hook）。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  DEFAULT_STREAM_JOB_STATE_FILTER,
  STREAM_JOB_FOCUS_STATES,
} from '../utils/streamJobMonitorFilters'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('StreamMonitor job list adoption', () => {
  it('keeps focus-state contract for ops noise reduction', () => {
    expect(DEFAULT_STREAM_JOB_STATE_FILTER).toBe('focus')
    expect([...STREAM_JOB_FOCUS_STATES].sort()).toEqual(['active', 'ready_to_deploy'])
  })

  it('StreamMonitor adopts shared filters, default focus, and resizable columns', () => {
    const src = read('pages/StreamMonitor.tsx')
    expect(src).toContain("from '../utils/streamJobMonitorFilters'")
    expect(src).toContain('DEFAULT_STREAM_JOB_STATE_FILTER')
    expect(src).toContain('STREAM_JOB_STATE_FILTER_OPTIONS')
    expect(src).toContain('matchStreamJobStateFilter')
    expect(src).toContain('useResizableTableColumns')
    expect(src).toContain('dw-resizable-table')
    expect(src).toContain('gido.stream.monitor.jobCols')
    // 默认关注中，禁止退回「无筛选展示全部含停止/失败」
    expect(src).toMatch(/useState<string\s*\|\s*undefined>\(\s*DEFAULT_STREAM_JOB_STATE_FILTER\s*\)/)
    expect(src).toContain('默认隐藏已停止与历史失败')

    const filters = read('utils/streamJobMonitorFilters.ts')
    expect(filters).toContain("value: 'focus'")
    expect(filters).toContain('关注中（运行/待部署）')
  })

  it('batch Workflow still owns the shared resizable hook (style consistency)', () => {
    const wf = read('pages/Workflow.tsx')
    expect(wf).toContain('useResizableTableColumns')
    expect(wf).toContain('dw-resizable-table')
    const hook = read('hooks/useResizableTableColumns.tsx')
    expect(hook).toContain('ResizableColumnTitle')
  })
})

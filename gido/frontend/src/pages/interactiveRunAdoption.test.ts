/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { nextStudioRunTab } from '../utils/studioRunTabPolicy'

const root = resolve(__dirname, '..')
const read = (relative: string) => readFileSync(resolve(root, relative), 'utf8')

describe('durable interactive run adoption', () => {
  it('uses log-first Studio tab policy without overriding manual result choices', () => {
    expect(nextStudioRunTab({
      current: 'log',
      status: 'running',
      nodeType: 'SQL',
      result: null,
      manuallySelected: false,
    })).toBe('log')
    expect(nextStudioRunTab({
      current: 'log',
      status: 'success',
      nodeType: 'SQL',
      result: { columns: ['id'], rows: [] },
      manuallySelected: false,
    })).toBe('result')
    expect(nextStudioRunTab({
      current: 'log',
      status: 'success',
      nodeType: 'SQL',
      result: { columns: ['id'], rows: [[1]] },
      manuallySelected: true,
    })).toBe('log')
    expect(nextStudioRunTab({
      current: 'result',
      status: 'failed',
      nodeType: 'SQL',
      result: null,
      manuallySelected: true,
    })).toBe('log')
  })

  it('adopts the shared interactive dock in Studio and DAG node config', () => {
    const studio = read('pages/Studio.tsx')
    const modal = read('components/NodeConfigModal.tsx')
    const hook = read('hooks/useInteractiveRun.ts')
    const panel = read('components/LiveRunPanel.tsx')

    expect(studio).toContain('useInteractiveRun')
    expect(studio).toContain('studioApi.submitRun')
    expect(studio).toContain('<InteractiveRunDock')
    expect(studio).toContain("[activeNode.id]: 'log'")
    expect(studio).toContain('manuallySelected: true')
    expect(studio).toContain('nextStudioRunTab')
    expect(modal).toContain('useInteractiveRun')
    expect(modal).toContain('studioApi.submitRun')
    expect(modal).toContain('<InteractiveRunDock')
    expect(hook).toContain('cursorRef.current')
    expect(hook).toContain('cancel')
    expect(panel).toContain('暂停跟随')
    expect(panel).toContain('下载')
  })

  it('adopts the shared dock in Probe and run history', () => {
    const probe = read('pages/Probe.tsx')
    const stream = read('pages/StreamStudio.tsx')
    const history = read('pages/RunHistoryDetail.tsx')
    const api = read('api/index.ts')

    expect(probe).toContain('probeApi.submitRun')
    expect(probe).toContain('useInteractiveRun')
    expect(probe).toContain('<InteractiveRunDock')
    expect(history).toContain('<InteractiveRunDock')
    expect(api).toContain('/studio/nodes/${id}/runs')
    expect(api).toContain('/adhoc-runs/${id}/events')
    expect(stream).not.toContain('useInteractiveRun')
  })
})

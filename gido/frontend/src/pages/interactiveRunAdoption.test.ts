/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')
const read = (relative: string) => readFileSync(resolve(root, relative), 'utf8')

describe('durable interactive run adoption', () => {
  it('adopts the shared interactive dock in Studio and DAG node config', () => {
    const studio = read('pages/Studio.tsx')
    const modal = read('components/NodeConfigModal.tsx')
    const hook = read('hooks/useInteractiveRun.ts')
    const panel = read('components/LiveRunPanel.tsx')

    expect(studio).toContain('useInteractiveRun')
    expect(studio).toContain('studioApi.submitRun')
    expect(studio).toContain('<InteractiveRunDock')
    expect(studio).not.toContain('resultMap')
    expect(studio).not.toContain('nextStudioRunTab')
    expect(modal).toContain('useInteractiveRun')
    expect(modal).toContain('studioApi.submitRun')
    expect(modal).toContain('<InteractiveRunDock')
    expect(modal).not.toContain("import LiveRunPanel")
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
    for (const legacy of [
      'ProbeRunResult',
      'activeResultTab',
      'activeStmt',
      'QueryResultTable',
      'QueryResultPanel',
      'EditorResultDock',
      'normalizeQueryColumns',
      'exportRowsToCsv',
      'resultTableMeta',
      'resultColMeta',
      'exportCsv',
    ]) {
      expect(probe).not.toContain(legacy)
    }
    expect(history).toContain('<InteractiveRunDock')
    expect(api).toContain('/studio/nodes/${id}/runs')
    expect(api).toContain('/adhoc-runs/${id}/events')
    expect(api).not.toContain('/probe/query')
    expect(stream).not.toContain('useInteractiveRun')
  })

  it('keeps snapshot query and result rendering inside shared modules', () => {
    const studio = read('pages/Studio.tsx')
    const probe = read('pages/Probe.tsx')
    const dock = read('components/InteractiveRunDock.tsx')
    const api = read('api/index.ts')

    for (const page of [studio, probe]) {
      expect(page).not.toContain('QueryResultTable')
      expect(page).not.toContain('QueryResultPanel')
      expect(page).not.toContain('sortQueryRows')
      expect(page).not.toContain('columnFilterPredicate')
    }
    expect(dock).toContain('useInteractiveStatementQuery')
    expect(dock).toContain('<QueryResultPanel')
    expect(dock).toContain('aria-label="多列排序"')
    expect(dock).toContain('aria-label="跳转到页码"')
    expect(dock).toContain('goToPage')
    expect(dock).toContain('.slice(-2)')
    expect(api).toContain('/rows/query')
  })

  it('redeems authenticated workspace shares into the unified run history', () => {
    const app = read('App.tsx')
    const sharePage = read('pages/RunShareRedeem.tsx')

    expect(app).toContain('share/run/:token')
    expect(sharePage).toContain('adhocRunsApi.redeemShare(token)')
    expect(sharePage).toContain('R.batch.runHistory')
    expect(sharePage).toContain('setCurrentWorkspace')
  })
})

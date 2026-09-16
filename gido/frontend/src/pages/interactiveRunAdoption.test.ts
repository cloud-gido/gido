import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')
const read = (relative: string) => readFileSync(resolve(root, relative), 'utf8')

describe('durable interactive run adoption', () => {
  it('uses one run hook and live log panel in Studio and DAG node config', () => {
    const studio = read('pages/Studio.tsx')
    const modal = read('components/NodeConfigModal.tsx')
    const hook = read('hooks/useInteractiveRun.ts')
    const panel = read('components/LiveRunPanel.tsx')

    expect(studio).toContain('useInteractiveRun')
    expect(studio).toContain('studioApi.submitRun')
    expect(studio).toContain('<LiveRunPanel')
    expect(modal).toContain('useInteractiveRun')
    expect(modal).toContain('studioApi.submitRun')
    expect(modal).toContain('<LiveRunPanel')
    expect(hook).toContain('cursorRef.current')
    expect(hook).toContain('cancel')
    expect(panel).toContain('暂停跟随')
    expect(panel).toContain('下载')
  })

  it('moves Probe to async lifecycle while keeping Stream Studio result-oriented', () => {
    const probe = read('pages/Probe.tsx')
    const stream = read('pages/StreamStudio.tsx')
    const history = read('pages/RunHistoryDetail.tsx')
    const api = read('api/index.ts')

    expect(probe).toContain('probeApi.submitRun')
    expect(probe).toContain('useInteractiveRun')
    expect(history).toContain('<LiveRunPanel')
    expect(api).toContain('/studio/nodes/${id}/runs')
    expect(api).toContain('/adhoc-runs/${id}/logs')
    expect(stream).not.toContain('useInteractiveRun')
  })
})

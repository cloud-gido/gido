/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 调度契约上收：节点重试间隔 + 工作流失败策略 / 优先级 / Worker / 时区。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('schedule runtime policy adoption', () => {
  it('NodeConfigModal exposes retry interval', () => {
    const src = read('components/NodeConfigModal.tsx')
    expect(src).toContain('retry_interval_minutes')
    expect(src).toContain('失败重试间隔')
  })

  it('Workflow exposes schedule strategy fields', () => {
    const src = read('pages/Workflow.tsx')
    expect(src).toContain('failure_strategy')
    expect(src).toContain('process_priority')
    expect(src).toContain('worker_group')
    expect(src).toContain('schedule_timezone')
  })

  it('WorkspaceSettings exposes node runtime defaults', () => {
    const src = read('pages/WorkspaceSettings.tsx')
    expect(src).toContain('default_node_timeout_seconds')
    expect(src).toContain('default_node_retry_times')
    expect(src).toContain('default_node_retry_interval_minutes')
  })
})

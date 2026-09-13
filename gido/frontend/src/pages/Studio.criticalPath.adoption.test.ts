/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：数据开发进页关键路径仅 nodes+folders；审批后置；workflows 不进页 listAll。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('Studio critical-path request orchestration', () => {
  it('keeps enter-page load to nodes+folders; defers approvals; lazy workflows for DEPENDENT', () => {
    const studio = read('pages/Studio.tsx')

    expect(studio).toContain('studioApi.listNodes(wsId)')
    expect(studio).toContain('studioApi.listFolders(wsId)')
    expect(studio).toContain('scheduleDeferredApprovals')
    expect(studio).toContain('requestIdleCallback')
    expect(studio).toContain("activeNode?.node_type !== 'DEPENDENT'")
    expect(studio).toContain('peekCachedDatasources')

    // 进页 load 不得再 Promise.all 拉 datasources / approvals / workflows
    const loadFn = studio.match(/const load = async \(\) => \{[\s\S]*?\n  \}/)?.[0] || ''
    expect(loadFn).toContain('listNodes')
    expect(loadFn).toContain('listFolders')
    expect(loadFn).not.toContain('datasourceApi.list')
    expect(loadFn).not.toContain('approvalApi.list')
    expect(loadFn).not.toContain('workflowApi.listAll')
  })
})

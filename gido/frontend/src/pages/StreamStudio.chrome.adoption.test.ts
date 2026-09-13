/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：实时 Studio 树关键路径不含审批；审批 idle 后置。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('StreamStudio chrome deferral', () => {
  it('loads jobs+folders on critical path; defers pending approvals', () => {
    const src = read('pages/StreamStudio.tsx')
    expect(src).toContain('loadPendingApprovals')
    expect(src).toContain('requestIdleCallback')
    const loadFn = src.match(/const load = useCallback\(async \(showSpinner = true\) => \{[\s\S]*?\}, \[wsId\]\)/)?.[0] || ''
    expect(loadFn).toContain('listJobs')
    expect(loadFn).toContain('listFolders')
    expect(loadFn).not.toContain('approvalApi.list')
  })
})

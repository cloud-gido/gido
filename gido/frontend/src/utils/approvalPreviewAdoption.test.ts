/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 契约：发布审批预览 Drawer 为主入口，深链为辅入口。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('approval preview adoption', () => {
  it('Approval 页点击资源名打开预览 Drawer', () => {
    const src = read('pages/Approval.tsx')
    expect(src).toContain('ApprovalResourcePreviewDrawer')
    expect(src).toContain('setPreviewId')
    expect(src).toContain('approvalApi')
  })

  it('预览 Drawer 调用 approvalApi.preview 并提供深链 footer', () => {
    const src = read('components/ApprovalResourcePreviewDrawer.tsx')
    expect(src).toContain('approvalApi.preview')
    expect(src).toContain('approvalResourceOpenPath')
    expect(src).toContain('approvalResourceOpenLabel')
    expect(src).toContain('target="_blank"')
  })

  it('Workflow / StreamStudio / ServiceApisPage 支持审批深链 query', () => {
    const workflow = read('pages/Workflow.tsx')
    const stream = read('pages/StreamStudio.tsx')
    const apis = read('pages/service/ServiceApisPage.tsx')
    expect(workflow).toContain("searchParams.get('workflow_id')")
    expect(workflow).toContain('workflowDeepLinkDoneRef')
    expect(stream).toContain("searchParams.get('job_id')")
    expect(apis).toContain("searchParams.get('api_id')")
    expect(apis).toContain('apiDeepLinkDoneRef')
  })
})

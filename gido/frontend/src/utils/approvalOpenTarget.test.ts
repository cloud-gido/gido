/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { approvalResourceOpenPath, approvalResourceOpenLabel } from './approvalOpenTarget'
import { R } from '../routes'

describe('approvalOpenTarget', () => {
  it('maps resource types to deep links', () => {
    expect(approvalResourceOpenPath({ resource_type: 'studio_node', resource_id: 9 }))
      .toBe(`${R.batch.studio}?node_id=9`)
    expect(approvalResourceOpenPath({ resource_type: 'workflow', resource_id: 3 }))
      .toBe(`${R.batch.workflow}?workflow_id=3`)
    expect(approvalResourceOpenPath({ resource_type: 'stream_job', resource_id: 7 }))
      .toBe(`${R.stream.studio}?job_id=7`)
    expect(approvalResourceOpenPath({ resource_type: 'data_service_api', resource_id: 2 }))
      .toBe(`${R.service.apis}?api_id=2`)
  })

  it('returns human labels', () => {
    expect(approvalResourceOpenLabel('studio_node')).toContain('数据开发')
    expect(approvalResourceOpenLabel('workflow')).toContain('工作流')
  })
})

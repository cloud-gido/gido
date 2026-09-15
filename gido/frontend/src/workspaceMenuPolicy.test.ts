/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { R } from './routes'
import { P } from './perm'
import {
  canAccessBatchPath,
  canSeeBatchMenu,
  pathsAllowedForWorkspaceRole,
} from './workspaceMenuPolicy'

const opsUser = {
  permissions: [
    P.GIDO_BATCH_STUDIO_READ,
    P.GIDO_BATCH_OPERATION_READ,
    P.GIDO_BATCH_PROBE_READ,
  ],
}

describe('workspaceMenuPolicy alert vs operation', () => {
  it('space developer paths include alert alongside operation', () => {
    const paths = pathsAllowedForWorkspaceRole('developer')
    expect(paths).toContain(R.batch.operation)
    expect(paths).toContain(R.batch.alert)
    expect(paths).toContain(R.batch.approval)
  })

  it('space developer with operation:read can see and open alert center', () => {
    const ws = { my_role: 'developer' }
    expect(canSeeBatchMenu(opsUser, ws, R.batch.alert, P.GIDO_BATCH_OPERATION_READ)).toBe(true)
    expect(canAccessBatchPath(opsUser, ws, R.batch.alert)).toBe(true)
    expect(canSeeBatchMenu(opsUser, ws, R.batch.operation, P.GIDO_BATCH_OPERATION_READ)).toBe(true)
  })

  it('space viewer cannot open alert or operation', () => {
    const ws = { my_role: 'viewer' }
    expect(canAccessBatchPath(opsUser, ws, R.batch.alert)).toBe(false)
    expect(canAccessBatchPath(opsUser, ws, R.batch.operation)).toBe(false)
  })
})

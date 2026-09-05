/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { can, isPlatformAdmin, P, workspaceAdminBypassesPlatformPerm } from './perm'

describe('perm can()', () => {
  it('denies empty user or undefined code', () => {
    expect(can(null, P.GIDO_BATCH_STUDIO_WRITE)).toBe(false)
    expect(can({ permissions: [P.GIDO_BATCH_STUDIO_WRITE] }, undefined)).toBe(false)
  })

  it('platform admin bypasses all', () => {
    const admin = { is_admin: true, permissions: [] }
    expect(can(admin, P.GIDO_BATCH_STUDIO_WRITE)).toBe(true)
    expect(can(admin, P.SYSTEM_USER_DELETE)).toBe(true)
  })

  it('platform admin via role_code', () => {
    expect(isPlatformAdmin({ role_code: 'platform_admin', is_admin: false, permissions: [] })).toBe(true)
    expect(isPlatformAdmin({ role_code: 'developer', is_admin: false, permissions: [] })).toBe(false)
  })

  it('operator-like user: run yes, write no', () => {
    const ops = {
      permissions: [
        P.GIDO_BATCH_STUDIO_READ,
        P.GIDO_BATCH_STUDIO_RUN,
        P.GIDO_STREAM_READ,
        P.GIDO_STREAM_RUN,
        P.GIDO_SERVICE_READ,
        P.GIDO_SERVICE_RUN,
      ],
    }
    expect(can(ops, P.GIDO_BATCH_STUDIO_READ)).toBe(true)
    expect(can(ops, P.GIDO_BATCH_STUDIO_RUN)).toBe(true)
    expect(can(ops, P.GIDO_BATCH_STUDIO_WRITE)).toBe(false)
    expect(can(ops, P.GIDO_STREAM_RUN)).toBe(true)
    expect(can(ops, P.GIDO_STREAM_WRITE)).toBe(false)
    expect(can(ops, P.GIDO_SERVICE_WRITE)).toBe(false)
  })

  it('workspace admin bypasses gido business codes but not system', () => {
    const user = { permissions: [] }
    const ws = { my_role: 'admin' }
    expect(can(user, P.GIDO_BATCH_STUDIO_WRITE, ws)).toBe(true)
    expect(can(user, P.SYSTEM_USER_WRITE, ws)).toBe(false)
    expect(workspaceAdminBypassesPlatformPerm(P.GIDO_SERVICE_RUN)).toBe(true)
    expect(workspaceAdminBypassesPlatformPerm(P.SYSTEM_ROLE_READ)).toBe(false)
  })

  it('exposes studio write/run constants used by Studio UI', () => {
    expect(P.GIDO_BATCH_STUDIO_WRITE).toBe('gido:batch:studio:write')
    expect(P.GIDO_BATCH_STUDIO_RUN).toBe('gido:batch:studio:run')
  })
})

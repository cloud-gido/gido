/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { R } from './routes'
import { canAccessStreamPath, canEnterStreamProduct, canSeeStreamMenu } from './streamMenuPolicy'

const viewerWs = { my_role: 'viewer' }
const devWs = { my_role: 'developer' }

const streamUser = {
  permissions: ['gido:stream:read', 'gido:stream:write', 'gido:stream:run'],
}

describe('streamMenuPolicy', () => {
  it('blocks space viewer even with stream:read', () => {
    expect(canEnterStreamProduct(streamUser, viewerWs)).toBe(false)
    expect(canAccessStreamPath(streamUser, viewerWs, R.stream.studio)).toBe(false)
    expect(canSeeStreamMenu(streamUser, viewerWs, R.stream.monitor)).toBe(false)
  })

  it('allows space developer with stream:read', () => {
    expect(canEnterStreamProduct(streamUser, devWs)).toBe(true)
    expect(canAccessStreamPath(streamUser, devWs, R.stream.studio)).toBe(true)
    expect(canAccessStreamPath(streamUser, devWs, R.stream.resourcesJars)).toBe(true)
  })

  it('platform admin bypasses space floor', () => {
    const admin = { is_admin: true, permissions: [] }
    expect(canEnterStreamProduct(admin, viewerWs)).toBe(true)
    expect(canAccessStreamPath(admin, viewerWs, R.stream.monitor)).toBe(true)
  })
})

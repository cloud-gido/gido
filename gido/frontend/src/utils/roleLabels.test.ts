/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  platformIdentityLabel,
  platformRoleOptionLabel,
  spaceMemberRoleLabel,
} from './roleLabels'

describe('roleLabels', () => {
  it('maps workspace member roles to Chinese labels', () => {
    expect(spaceMemberRoleLabel('admin')).toBe('空间管理员')
    expect(spaceMemberRoleLabel('developer')).toBe('开发者')
    expect(spaceMemberRoleLabel('viewer')).toBe('只读')
    expect(spaceMemberRoleLabel(null)).toBe('—')
  })

  it('marks platform manager roles in option labels', () => {
    expect(platformRoleOptionLabel({ code: 'super_admin', name: '超级管理员' })).toBe('超级管理员（平台管理）')
    expect(platformRoleOptionLabel({ code: 'developer', name: '开发工程师' })).toBe('开发工程师')
  })

  it('prefers role_name for header identity', () => {
    expect(platformIdentityLabel({ role_name: '超级管理员', role_code: 'super_admin', is_admin: true })).toBe('超级管理员')
    expect(platformIdentityLabel({ role_name: null, role_code: 'platform_admin' })).toBe('平台管理员')
    expect(platformIdentityLabel({ role_name: null, role_code: 'developer' })).toBe(null)
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  headerAccountRoleLabel,
  platformIdentityLabel,
  platformRoleOptionLabel,
  spaceMemberRoleLabel,
  workspaceSwitcherLabel,
} from './roleLabels'

describe('roleLabels', () => {
  it('maps workspace member roles to Chinese labels', () => {
    expect(spaceMemberRoleLabel('admin')).toBe('空间管理员')
    expect(spaceMemberRoleLabel('developer')).toBe('开发者')
    expect(spaceMemberRoleLabel('viewer')).toBe('只读')
    expect(spaceMemberRoleLabel(null)).toBe('—')
  })

  it('workspace switcher shows space role as the primary chrome identity', () => {
    expect(workspaceSwitcherLabel({ name: 'infras', my_role: 'developer' })).toBe('infras · 开发者')
    expect(workspaceSwitcherLabel({ name: 'infras', my_role: 'admin' })).toBe('infras · 空间管理员')
    expect(workspaceSwitcherLabel({ name: 'infras' })).toBe('infras')
  })

  it('account chip mirrors current workspace role only', () => {
    expect(headerAccountRoleLabel({ my_role: 'developer' })).toBe('开发者')
    expect(headerAccountRoleLabel({ my_role: null })).toBe(null)
  })

  it('marks platform manager roles in option labels', () => {
    expect(platformRoleOptionLabel({ code: 'super_admin', name: '超级管理员' })).toBe('超级管理员（平台管理）')
    expect(platformRoleOptionLabel({ code: 'workspace_steward', name: '数据源管家' })).toBe('数据源管家（平台角色·非空间成员）')
  })

  it('platformIdentityLabel is raw name for account menu, not chrome', () => {
    expect(platformIdentityLabel({ role_name: '数据分析 (只读)', role_code: 'analyst' })).toBe('数据分析 (只读)')
    expect(platformIdentityLabel({ role_name: '超级管理员', role_code: 'super_admin', is_admin: true })).toBe('超级管理员')
    expect(platformIdentityLabel({ role_name: null, role_code: 'platform_admin' })).toBe('平台管理员')
    expect(platformIdentityLabel({ role_name: null, role_code: 'developer' })).toBe(null)
  })
})

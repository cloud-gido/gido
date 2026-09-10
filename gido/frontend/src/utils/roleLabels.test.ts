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
  workspaceSwitcherTitle,
} from './roleLabels'

describe('roleLabels', () => {
  it('maps workspace member roles to Chinese labels', () => {
    expect(spaceMemberRoleLabel('admin')).toBe('空间管理员')
    expect(spaceMemberRoleLabel('developer')).toBe('开发者')
    expect(spaceMemberRoleLabel('viewer')).toBe('只读')
    expect(spaceMemberRoleLabel(null)).toBe('—')
  })

  it('workspace switcher label is space name only (role lives in account chip)', () => {
    expect(workspaceSwitcherLabel({ name: 'infras', my_role: 'developer' })).toBe('infras')
    expect(workspaceSwitcherLabel({ name: 'infras', my_role: 'admin' })).toBe('infras')
    expect(workspaceSwitcherLabel({ name: 'infras' })).toBe('infras')
    expect(workspaceSwitcherLabel({ name: '  ads  ', my_role: 'viewer' })).toBe('ads')
    expect(workspaceSwitcherLabel(null)).toBe('未命名空间')
  })

  it('workspace switcher title points role to account area', () => {
    expect(workspaceSwitcherTitle({ name: 'infras', my_role: 'admin' })).toContain('空间管理员')
    expect(workspaceSwitcherTitle({ name: 'infras', my_role: 'admin' })).toContain('右上角')
    expect(workspaceSwitcherTitle({ name: 'infras' })).toContain('切换工作空间')
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

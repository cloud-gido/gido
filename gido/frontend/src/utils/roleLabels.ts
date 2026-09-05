/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 全站角色展示用词统一：
 * - 平台角色：账号级，用户管理 / 顶栏身份
 * - 空间成员角色：工作空间内，成员表 / 空间切换器
 * - 负责人：空间属性（owner），不是第三种权限角色
 */

/** 工作空间成员角色（与后端 WorkspaceMember.role 一致） */
export const SPACE_MEMBER_ROLE_LABELS: Record<string, string> = {
  admin: '空间管理员',
  developer: '开发者',
  viewer: '只读',
}

export const SPACE_MEMBER_ROLE_OPTS = [
  {
    value: 'admin',
    label: SPACE_MEMBER_ROLE_LABELS.admin,
    title: '本空间业务全权限 + 空间设置；不含平台用户/集成管理',
  },
  {
    value: 'developer',
    label: SPACE_MEMBER_ROLE_LABELS.developer,
    title: '本空间开发/集成/运维等；不含空间设置与平台系统管理',
  },
  {
    value: 'viewer',
    label: SPACE_MEMBER_ROLE_LABELS.viewer,
    title: '本空间只读（探查/字典等，视平台权限而定）',
  },
]

export function spaceMemberRoleLabel(role?: string | null): string {
  if (!role) return '—'
  return SPACE_MEMBER_ROLE_LABELS[role] || role
}

/** 平台管理类角色（与后端 PLATFORM_MANAGER_ROLE_CODES 对齐） */
export function isPlatformManagerRoleCode(code?: string | null): boolean {
  return code === 'super_admin' || code === 'platform_admin'
}

/** 用户管理下拉：平台角色展示名 */
export function platformRoleOptionLabel(role: { code?: string; name?: string }): string {
  const name = role.name || role.code || '未命名角色'
  if (isPlatformManagerRoleCode(role.code)) return `${name}（平台管理）`
  return name
}

/** 顶栏等：优先显示平台角色中文名 */
export function platformIdentityLabel(user?: {
  role_name?: string | null
  role_code?: string | null
  is_admin?: boolean
} | null): string | null {
  if (!user) return null
  if (user.role_name) return user.role_name
  if (isPlatformManagerRoleCode(user.role_code) || user.is_admin) return '平台管理员'
  return null
}

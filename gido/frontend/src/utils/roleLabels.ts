/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 角色展示约定（鉴权仍两层，顶栏只突出当前上下文）：
 * - 空间切换器：只显示空间名（回答「在哪个空间」）
 * - 顶栏账号区：只显示本空间成员角色（回答「我是谁」）；三端共用 WorkspaceHeaderBar
 * - 平台角色：仅出现在账号菜单「平台权限」、用户管理；不当日常第二身份并排展示
 * - 负责人：空间归属属性（owner），不是第三种权限角色
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

/** 空间切换器主文案：仅空间名（批 / 流 / 服顶栏共用） */
export function workspaceSwitcherLabel(workspace?: {
  name?: string | null
  my_role?: string | null
} | null): string {
  return (workspace?.name || '').trim() || '未命名空间'
}

/** 空间切换器悬停：角色在右上角账号区，这里仅作发现提示 */
export function workspaceSwitcherTitle(workspace?: {
  name?: string | null
  my_role?: string | null
} | null): string {
  const name = workspaceSwitcherLabel(workspace)
  const role = headerAccountRoleLabel(workspace)
  return role ? `${name} · 当前角色见右上角（${role}）` : `${name} · 切换工作空间`
}

/** 顶栏账号副标题：只显示当前空间角色，避免与平台角色并排误解 */
export function headerAccountRoleLabel(workspace?: {
  my_role?: string | null
  name?: string | null
} | null): string | null {
  if (!workspace?.my_role) return null
  return spaceMemberRoleLabel(workspace.my_role)
}

/**
 * 菜单内「当前空间」说明：空间名 · 空间角色。
 * 不用于顶栏主文案。
 */
export function formatSpaceContextLabel(workspace?: {
  name?: string | null
  my_role?: string | null
} | null): string | null {
  if (!workspace?.my_role) return null
  const name = (workspace.name || '').trim() || '未命名空间'
  return `${name} · ${spaceMemberRoleLabel(workspace.my_role)}`
}

/** 平台管理类角色（与后端 PLATFORM_MANAGER_ROLE_CODES 对齐） */
export function isPlatformManagerRoleCode(code?: string | null): boolean {
  return code === 'super_admin' || code === 'platform_admin'
}

/** 用户管理下拉：平台角色展示名 */
export function platformRoleOptionLabel(role: { code?: string; name?: string }): string {
  const name = role.name || role.code || '未命名角色'
  if (isPlatformManagerRoleCode(role.code)) return `${name}（平台管理）`
  if (role.code === 'workspace_steward') {
    return `${name}（平台角色·非空间成员）`
  }
  return name
}

/**
 * 平台账号角色原始展示名（用户管理 / 账号菜单「平台权限」）。
 * 不用于顶栏主文案。
 */
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

/** 顶栏：空间角色；菜单可用 platform 作补充 */
export function formatUserContextLabel(
  user?: {
    role_name?: string | null
    role_code?: string | null
    is_admin?: boolean
  } | null,
  workspace?: {
    name?: string | null
    my_role?: string | null
  } | null,
): { platform: string | null; space: string | null } {
  return {
    platform: platformIdentityLabel(user),
    space: headerAccountRoleLabel(workspace),
  }
}

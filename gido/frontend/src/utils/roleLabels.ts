/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 角色展示约定（鉴权仍两层，界面口径对齐）：
 * - 平台账号角色 →「平台角色」（用户管理 / 顶栏副标题）
 * - 空间成员角色 →「空间角色」（空间切换器 / 顶栏次要行 / 工作空间成员）
 * - 负责人：空间归属属性（owner），不是第三种权限角色
 * - 禁止对外说光秃秃的「管理员」——说清是平台管理还是空间管理员
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

/** 空间切换器主文案：名称 · 空间角色（批 / 流 / 服顶栏共用） */
export function workspaceSwitcherLabel(workspace?: {
  name?: string | null
  my_role?: string | null
} | null): string {
  const name = (workspace?.name || '').trim() || '未命名空间'
  if (!workspace?.my_role) return name
  return `${name} · ${spaceMemberRoleLabel(workspace.my_role)}`
}

/** 空间切换器悬停补充 */
export function workspaceSwitcherTitle(workspace?: {
  name?: string | null
  my_role?: string | null
} | null): string {
  const name = (workspace?.name || '').trim() || '未命名空间'
  if (!workspace?.my_role) return `${name} · 切换工作空间`
  return `${name} · 空间角色 ${spaceMemberRoleLabel(workspace.my_role)}`
}

/**
 * 当前空间上下文行：`infras · 开发者`。
 * 无空间成员角色时返回 null（平台代管账号也可能无成员行）。
 */
export function formatSpaceContextLabel(workspace?: {
  name?: string | null
  my_role?: string | null
} | null): string | null {
  if (!workspace?.my_role) return null
  const name = (workspace.name || '').trim() || '未命名空间'
  return `${name} · ${spaceMemberRoleLabel(workspace.my_role)}`
}

/** @deprecated 使用 formatSpaceContextLabel；保留别名以免旧引用断裂 */
export function headerAccountRoleLabel(workspace?: {
  my_role?: string | null
  name?: string | null
} | null): string | null {
  return formatSpaceContextLabel(workspace)
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
 * 平台账号角色展示名（顶栏副标题 / 账号菜单「平台角色」）。
 * 优先 role_name；平台管理类无名称时回退「平台管理员」。
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

/** 顶栏双身份：平台角色 + 当前空间上下文 */
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
    space: formatSpaceContextLabel(workspace),
  }
}

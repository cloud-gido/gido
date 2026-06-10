/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export type WorkspacePermContext = { my_role?: string | null } | null | undefined

/**
 * 当前工作空间内的空间管理员在本空间视同具备业务权限（不含 system:* 与全局新建空间 workspace:write）。
 * 服务端以 assert_workspace_data_capability 为准。
 */
export function workspaceAdminBypassesPlatformPerm(code: string): boolean {
  if (code.startsWith('system:')) return false
  if (code === 'workspace:write') return false
  return (
    code.startsWith('gido:') ||
    code.startsWith('audit:') ||
    code === 'workspace:read' ||
    code === 'workspace:member:manage'
  )
}

/**
 * 与后端 is_platform_admin / 平台角色对齐。
 * 客户端仅影响菜单显隐，真实鉴权在后端。
 */
export function isPlatformAdmin(user: any): boolean {
  if (!user) return false
  if (user.is_admin === true || user.is_admin === 1) return true
  if (user.username === 'admin' && user.is_admin !== false) return true
  const rc = user.role_code
  if (rc === 'super_admin' || rc === 'platform_admin') return true
  return false
}

export function isWorkspaceAdmin(user: any, workspace?: WorkspacePermContext): boolean {
  if (isPlatformAdmin(user)) return true
  return workspace?.my_role === 'admin'
}

export const P = {
  SYSTEM_USER_READ: 'system:user:read',
  SYSTEM_USER_WRITE: 'system:user:write',
  SYSTEM_USER_DELETE: 'system:user:delete',
  SYSTEM_ROLE_READ: 'system:role:read',
  SYSTEM_ROLE_WRITE: 'system:role:write',
  SYSTEM_ROLE_DELETE: 'system:role:delete',
  SYSTEM_INTEGRATION_READ: 'system:integration:read',
  SYSTEM_INTEGRATION_WRITE: 'system:integration:write',
  WORKSPACE_READ: 'workspace:read',
  WORKSPACE_MEMBER_MANAGE: 'workspace:member:manage',
  GIDO_BATCH_STUDIO_READ: 'gido:batch:studio:read',
  GIDO_BATCH_WORKFLOW_READ: 'gido:batch:workflow:read',
  GIDO_BATCH_DATAMAP_READ: 'gido:batch:datamap:read',
  GIDO_BATCH_PROBE_READ: 'gido:batch:probe:read',
  GIDO_BATCH_QUALITY_READ: 'gido:batch:quality:read',
  GIDO_BATCH_INTEGRATION_READ: 'gido:batch:integration:read',
  GIDO_BATCH_INTEGRATION_WRITE: 'gido:batch:integration:write',
  GIDO_BATCH_INTEGRATION_RUN: 'gido:batch:integration:run',
  GIDO_BATCH_OPERATION_READ: 'gido:batch:operation:read',
  GIDO_BATCH_DATASOURCE_READ: 'gido:batch:datasource:read',
  GIDO_SERVICE_READ: 'gido:service:read',
  GIDO_SERVICE_WRITE: 'gido:service:write',
  GIDO_SERVICE_RUN: 'gido:service:run',
  GIDO_STREAM_READ: 'gido:stream:read',
  GIDO_STREAM_WRITE: 'gido:stream:write',
  GIDO_STREAM_RUN: 'gido:stream:run',
  AUDIT_READ: 'audit:read',
} as const

export function can(user: any, code: string, workspace?: WorkspacePermContext): boolean {
  if (!user) return false
  if (isPlatformAdmin(user)) return true
  const list: string[] = Array.isArray(user.permissions) ? user.permissions : []
  if (list.includes('*')) return true
  if (workspace?.my_role === 'admin' && workspaceAdminBypassesPlatformPerm(code))
    return true
  return list.includes(code)
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/**
 * 侧栏可见性 = 平台权限码 ∩ 工作空间成员角色（my_role）。
 */
import { R } from './routes'
import { can, isPlatformAdmin, P, type WorkspacePermContext } from './perm'

export type WorkspaceMemberRole = 'admin' | 'developer' | 'viewer' | 'none' | string

const DEVELOPER_PATHS: string[] = [
  R.batch.studio,
  R.batch.workflow,
  R.batch.datamap,
  R.batch.probe,
  R.batch.quality,
  R.batch.integration,
  R.batch.operation,
  R.batch.approval,
  R.batch.datasource,
]

const VIEWER_PATHS: string[] = [R.batch.probe, R.batch.datamap]

const ADMIN_EXTRA_PATHS: string[] = [R.batch.workspaceSettings]

export function pathsAllowedForWorkspaceRole(role: WorkspaceMemberRole | null | undefined): string[] {
  if (role === 'admin') return [...DEVELOPER_PATHS, ...ADMIN_EXTRA_PATHS]
  if (role === 'developer') return DEVELOPER_PATHS
  if (role === 'viewer') return VIEWER_PATHS
  return []
}

export function workspaceRoleAllowsPath(role: WorkspaceMemberRole | null | undefined, path: string): boolean {
  if (path === R.batch.admin || path === R.batch.systemIntegration) return false
  if (path === R.batch.legacyService) return false
  return pathsAllowedForWorkspaceRole(role).includes(path)
}

type PermGate = string | string[]

function gatePerm(user: any, perm: PermGate, workspace?: WorkspacePermContext): boolean {
  return Array.isArray(perm) ? perm.some(x => can(user, x, workspace)) : can(user, perm, workspace)
}

export function canSeeBatchMenu(
  user: any,
  workspace: WorkspacePermContext,
  path: string,
  perm: PermGate,
): boolean {
  if (!gatePerm(user, perm, workspace)) return false
  if (isPlatformAdmin(user)) return true

  if (path === R.batch.admin || path === R.batch.systemIntegration) {
    return gatePerm(user, perm, workspace)
  }

  const role = workspace?.my_role
  if (!role || role === 'none') return false
  return workspaceRoleAllowsPath(role, path)
}

export function defaultBatchHome(user: any, workspace?: WorkspacePermContext): string {
  if (isPlatformAdmin(user)) return R.batch.studio
  const role = workspace?.my_role
  if (role === 'viewer') return R.batch.probe
  if (can(user, P.GIDO_BATCH_STUDIO_READ, workspace) && role !== 'viewer') return R.batch.studio
  if (can(user, P.GIDO_BATCH_PROBE_READ, workspace)) return R.batch.probe
  if (can(user, P.GIDO_BATCH_DATAMAP_READ, workspace)) return R.batch.datamap
  return R.batch.probe
}

export function canAccessBatchPath(
  user: any,
  workspace: WorkspacePermContext,
  pathname: string,
): boolean {
  if (isPlatformAdmin(user)) return true
  const path = pathname.replace(/\/+$/, '') || R.batch.root

  const routePerm: Record<string, PermGate> = {
    [R.batch.studio]: P.GIDO_BATCH_STUDIO_READ,
    [R.batch.workflow]: P.GIDO_BATCH_WORKFLOW_READ,
    [R.batch.datamap]: P.GIDO_BATCH_DATAMAP_READ,
    [R.batch.probe]: P.GIDO_BATCH_PROBE_READ,
    [R.batch.quality]: P.GIDO_BATCH_QUALITY_READ,
    [R.batch.integration]: P.GIDO_BATCH_INTEGRATION_READ,
    [R.batch.operation]: P.GIDO_BATCH_OPERATION_READ,
    [R.batch.approval]: P.GIDO_BATCH_OPERATION_READ,
    [R.batch.datasource]: P.GIDO_BATCH_DATASOURCE_READ,
    [R.batch.workspaceSettings]: P.GIDO_BATCH_DATASOURCE_READ,
    [R.batch.admin]: [P.SYSTEM_ROLE_READ, P.SYSTEM_INTEGRATION_READ],
    [R.batch.systemIntegration]: P.SYSTEM_INTEGRATION_READ,
  }

  const perm = routePerm[path]
  if (!perm) return path === R.batch.root
  return canSeeBatchMenu(user, workspace, path, perm)
}

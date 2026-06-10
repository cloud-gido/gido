/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { R } from './routes'
import { can, isPlatformAdmin, P, type WorkspacePermContext } from './perm'

export type WorkspaceMemberRole = 'admin' | 'developer' | 'viewer' | 'none' | string

type PermGate = string | string[]

const SERVICE_DEVELOPER_PATHS: string[] = [
  R.service.overview,
  R.service.apis,
  R.service.apps,
  R.service.monitor,
  R.service.gateway,
  R.service.datasource,
]

const SERVICE_VIEWER_PATHS: string[] = [
  R.service.overview,
  R.service.gateway,
  R.service.approval,
]

function gatePerm(user: any, perm: PermGate, workspace?: WorkspacePermContext): boolean {
  return Array.isArray(perm) ? perm.some(x => can(user, x, workspace)) : can(user, perm, workspace)
}

export function servicePathsForRole(role: WorkspaceMemberRole | null | undefined): string[] {
  if (role === 'admin' || role === 'developer') return SERVICE_DEVELOPER_PATHS
  if (role === 'viewer') return SERVICE_VIEWER_PATHS
  return []
}

export function canSeeServiceMenu(
  user: any,
  workspace: WorkspacePermContext,
  path: string,
  perm: PermGate,
): boolean {
  if (!gatePerm(user, perm, workspace)) return false
  if (isPlatformAdmin(user)) return true
  const role = workspace?.my_role
  if (!role || role === 'none') return false
  return servicePathsForRole(role).includes(path)
}

const ROUTE_PERM: Record<string, PermGate> = {
  [R.service.overview]: P.GIDO_SERVICE_READ,
  [R.service.apis]: P.GIDO_SERVICE_READ,
  [R.service.apps]: P.GIDO_SERVICE_READ,
  [R.service.monitor]: P.GIDO_SERVICE_READ,
  [R.service.gateway]: P.GIDO_SERVICE_READ,
  [R.service.approval]: P.GIDO_SERVICE_READ,
  [R.service.datasource]: P.GIDO_BATCH_DATASOURCE_READ,
}

export function canAccessServicePath(
  user: any,
  workspace: WorkspacePermContext,
  pathname: string,
): boolean {
  if (isPlatformAdmin(user)) return true
  const path = pathname.replace(/\/+$/, '') || R.service.root
  const perm = ROUTE_PERM[path]
  if (!perm) return path === R.service.root
  return canSeeServiceMenu(user, workspace, path, perm)
}

export function defaultServiceHome(user: any, workspace?: WorkspacePermContext): string {
  if (can(user, P.GIDO_SERVICE_WRITE, workspace) || can(user, P.GIDO_SERVICE_RUN, workspace)) {
    return R.service.apis
  }
  if (can(user, P.GIDO_SERVICE_READ, workspace)) return R.service.overview
  return R.service.gateway
}

export function canEnterServiceProduct(user: any, workspace?: WorkspacePermContext): boolean {
  return can(user, P.GIDO_SERVICE_READ, workspace)
}

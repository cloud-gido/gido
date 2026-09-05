/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 实时产品菜单/路由双门禁：平台 stream 权限 + 空间成员地板（与 Batch API 的 developer 下限对齐）。
 */
import { R } from './routes'
import { can, isPlatformAdmin, P, type WorkspacePermContext } from './perm'

export type WorkspaceMemberRole = 'admin' | 'developer' | 'viewer' | 'none' | string

type PermGate = string | string[]

/** 与后端 streaming require_* 的 min_member_role=developer 对齐；viewer 不可进实时产品 */
const STREAM_DEVELOPER_PATHS: string[] = [
  R.stream.studio,
  R.stream.pipelines,
  R.stream.resources,
  R.stream.resourcesJars,
  R.stream.resourcesConnectors,
  R.stream.resourcesFiles,
  R.stream.monitor,
  R.stream.approval,
]

function gatePerm(user: any, perm: PermGate, workspace?: WorkspacePermContext): boolean {
  return Array.isArray(perm) ? perm.some(x => can(user, x, workspace)) : can(user, perm, workspace)
}

export function streamPathsForRole(role: WorkspaceMemberRole | null | undefined): string[] {
  if (role === 'admin' || role === 'developer') return STREAM_DEVELOPER_PATHS
  return []
}

export function canSeeStreamMenu(
  user: any,
  workspace: WorkspacePermContext,
  path: string,
  perm: PermGate = P.GIDO_STREAM_READ,
): boolean {
  if (!gatePerm(user, perm, workspace)) return false
  if (isPlatformAdmin(user)) return true
  const role = workspace?.my_role
  if (!role || role === 'none') return false
  const normalized = path.replace(/\/+$/, '')
  const allowed = streamPathsForRole(role)
  if (allowed.includes(normalized)) return true
  // 资源子路径
  if (normalized.startsWith(`${R.stream.resources}/`)) {
    return allowed.includes(R.stream.resources)
  }
  return false
}

const ROUTE_PERM: Record<string, PermGate> = {
  [R.stream.studio]: P.GIDO_STREAM_READ,
  [R.stream.pipelines]: P.GIDO_STREAM_READ,
  [R.stream.resources]: P.GIDO_STREAM_READ,
  [R.stream.resourcesJars]: P.GIDO_STREAM_READ,
  [R.stream.resourcesConnectors]: P.GIDO_STREAM_READ,
  [R.stream.resourcesFiles]: P.GIDO_STREAM_READ,
  [R.stream.monitor]: P.GIDO_STREAM_READ,
  [R.stream.approval]: P.GIDO_STREAM_READ,
}

export function canAccessStreamPath(
  user: any,
  workspace: WorkspacePermContext,
  pathname: string,
): boolean {
  if (isPlatformAdmin(user)) return true
  const path = pathname.replace(/\/+$/, '') || R.stream.root
  if (path === R.stream.root) {
    return canEnterStreamProduct(user, workspace)
  }
  if (path.startsWith(`${R.stream.resources}/`)) {
    return canSeeStreamMenu(user, workspace, R.stream.resources, P.GIDO_STREAM_READ)
  }
  const perm = ROUTE_PERM[path]
  if (!perm) return false
  return canSeeStreamMenu(user, workspace, path, perm)
}

/** 产品切换器 / 路由守卫：须有 stream 读权限且空间角色 ≥ developer（或平台管理员） */
export function canEnterStreamProduct(user: any, workspace?: WorkspacePermContext): boolean {
  if (!can(user, P.GIDO_STREAM_READ, workspace)) return false
  if (isPlatformAdmin(user)) return true
  const role = workspace?.my_role
  return role === 'admin' || role === 'developer'
}

export function defaultStreamHome(user: any, workspace?: WorkspacePermContext): string {
  if (canEnterStreamProduct(user, workspace)) return R.stream.studio
  return R.batch.root
}

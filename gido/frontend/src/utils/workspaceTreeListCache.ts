/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据开发 / 实时 Studio 侧栏：同 SPA 会话内内存缓存（对齐 datasourceListCache）。
 * 成熟 SaaS 对「服务端权威」列表常用 stale-while-revalidate：二次进页先画缓存，再静默刷新。
 * 不写 sessionStorage——硬刷新回到 skeleton 是预期；持久化易展示已删节点等过期数据。
 * Probe 例外：树本身可本地权威，仍用 probeLocalStore。
 */
export type WorkspaceTreeListKind = 'studio' | 'stream'

export type WorkspaceTreeListSnapshot = {
  folders: unknown[]
  leaves: unknown[]
}

const treeListCache = new Map<string, WorkspaceTreeListSnapshot>()

function cacheKey(kind: WorkspaceTreeListKind, wsId: number) {
  return `${kind}:${wsId}`
}

export function loadTreeListCache(
  kind: WorkspaceTreeListKind,
  wsId: number | undefined | null,
): WorkspaceTreeListSnapshot | null {
  if (wsId == null) return null
  return treeListCache.get(cacheKey(kind, wsId)) ?? null
}

export function saveTreeListCache(
  kind: WorkspaceTreeListKind,
  wsId: number,
  snapshot: WorkspaceTreeListSnapshot,
) {
  treeListCache.set(cacheKey(kind, wsId), {
    folders: Array.isArray(snapshot.folders) ? snapshot.folders : [],
    leaves: Array.isArray(snapshot.leaves) ? snapshot.leaves : [],
  })
}

/** 有内存快照则侧栏可立刻画树，不必等 list API */
export function treeListReadyFromCache(
  kind: WorkspaceTreeListKind,
  wsId: number | undefined | null,
): boolean {
  if (wsId == null) return false
  return treeListCache.has(cacheKey(kind, wsId))
}

/** 测试用 */
export function clearTreeListCacheForTests() {
  treeListCache.clear()
}

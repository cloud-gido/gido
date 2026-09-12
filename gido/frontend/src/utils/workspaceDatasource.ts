/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export type DatasourceRow = { id: number; name: string; ds_type?: string }

export type WorkspaceDatasourceCtx = {
  default_datasource_id?: number | null
  warehouse_datasource_id?: number | null
  effective_warehouse_datasource_id?: number | null
}

/** 是否已在脚本/节点上「单独配置」过数据源（非继承） */
export function hasExplicitDatasource(explicitId: number | null | undefined): boolean {
  return explicitId != null && explicitId > 0
}

/** 运行/探查时实际使用的数据源 id */
export function resolveEffectiveDatasourceId(
  explicitId: number | null | undefined,
  workspace: WorkspaceDatasourceCtx | null | undefined,
): number | null {
  if (hasExplicitDatasource(explicitId)) return explicitId!
  const def = workspace?.default_datasource_id
  if (def != null && def > 0) return def
  const wh = workspace?.warehouse_datasource_id ?? workspace?.effective_warehouse_datasource_id
  return wh != null && wh > 0 ? wh : null
}

export function findDatasource(
  datasources: DatasourceRow[],
  id: number | null | undefined,
): DatasourceRow | null {
  if (id == null) return null
  return datasources.find(d => d.id === id) ?? null
}

export type DatasourceResolveInfo = {
  effectiveId: number | null
  effective: DatasourceRow | null
  source: 'explicit' | 'workspace' | 'none'
  explicit: DatasourceRow | null
}

/**
 * 解析运行数据源。即使 datasources 列表尚未拉到，只要空间/节点已有 id，
 * 也不应短暂显示「未配置」（避免探查打开时 Tag/占位符闪烁）。
 */
export function resolveDatasourceForRun(
  explicitId: number | null | undefined,
  workspace: WorkspaceDatasourceCtx | null | undefined,
  datasources: DatasourceRow[],
): DatasourceResolveInfo {
  const effectiveId = resolveEffectiveDatasourceId(explicitId, workspace)
  const explicit = hasExplicitDatasource(explicitId) ? findDatasource(datasources, explicitId) : null
  const effective = findDatasource(datasources, effectiveId)
  let source: DatasourceResolveInfo['source'] = 'none'
  if (hasExplicitDatasource(explicitId)) source = 'explicit'
  else if (effectiveId != null) source = 'workspace'
  return { effectiveId, effective, source, explicit }
}

export function datasourceTagText(info: DatasourceResolveInfo): string {
  if (info.source === 'explicit') {
    if (info.explicit) {
      return `查询固定 ${info.explicit.name} (${info.explicit.ds_type || '—'})`
    }
    return '查询指定数据源'
  }
  if (info.source === 'workspace') {
    if (info.effective) {
      return `空间默认 ${info.effective.name} (${info.effective.ds_type || '—'})`
    }
    return '空间默认'
  }
  return '未配置数据源'
}

/** 同 SPA 会话内按空间缓存数据源列表，避免探查/Studio 重复进入时先空再满闪一下 */
const datasourceListCache = new Map<number, DatasourceRow[]>()

export function peekCachedDatasources(workspaceId: number | null | undefined): DatasourceRow[] {
  if (workspaceId == null) return []
  return datasourceListCache.get(workspaceId) || []
}

export function rememberDatasources(workspaceId: number, list: DatasourceRow[]): void {
  datasourceListCache.set(workspaceId, list)
}

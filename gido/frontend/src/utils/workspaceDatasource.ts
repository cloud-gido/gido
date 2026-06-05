/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/**
 * 工作空间数据源策略（与后端 studio_sql_run.resolve_sql_datasource 一致）：
 * - 脚本/节点已保存 datasource_id → 固定使用该数据源
 * - 未保存（空）→ 运行与展示时继承工作空间「默认数据源」
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

export function resolveDatasourceForRun(
  explicitId: number | null | undefined,
  workspace: WorkspaceDatasourceCtx | null | undefined,
  datasources: DatasourceRow[],
): DatasourceResolveInfo {
  const explicit = hasExplicitDatasource(explicitId) ? findDatasource(datasources, explicitId) : null
  const effectiveId = resolveEffectiveDatasourceId(explicitId, workspace)
  const effective = findDatasource(datasources, effectiveId)
  let source: DatasourceResolveInfo['source'] = 'none'
  if (explicit) source = 'explicit'
  else if (effective) source = 'workspace'
  return { effectiveId, effective, source, explicit }
}

export function datasourceTagText(info: DatasourceResolveInfo): string {
  if (info.source === 'explicit' && info.explicit) {
    return `节点固定 ${info.explicit.name} (${info.explicit.ds_type || '—'})`
  }
  if (info.source === 'workspace' && info.effective) {
    return `空间默认 ${info.effective.name} (${info.effective.ds_type || '—'})`
  }
  return '未配置数据源'
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 作业运维列表筛选（纯函数，便于单测；UI 在 StreamMonitor）。
 */

/** 运维默认视图：运行中（含部署/恢复中）+ 待部署 */
export const STREAM_JOB_FOCUS_STATES = new Set(['active', 'ready_to_deploy'])

/** 状态下拉默认值：关注中 */
export const DEFAULT_STREAM_JOB_STATE_FILTER = 'focus'

export const STREAM_JOB_STATE_FILTER_OPTIONS = [
  { value: 'focus', label: '关注中（运行/待部署）' },
  { value: 'active', label: '运行中' },
  { value: 'ready_to_deploy', label: '已批准待部署' },
  { value: 'needs_attention', label: '需处理' },
  { value: 'stopped', label: '已停止' },
  { value: 'draft', label: '草稿' },
  { value: 'terminal', label: '已结束' },
] as const

/**
 * 是否通过「运行状态」筛选。
 * - undefined / 空：全部
 * - focus：仅 active + ready_to_deploy
 * - 其它：精确匹配 unifiedJobState().key
 */
export function matchStreamJobStateFilter(
  stateClass: string,
  stateFilter?: string | null,
): boolean {
  if (!stateFilter) return true
  if (stateFilter === 'focus') return STREAM_JOB_FOCUS_STATES.has(stateClass)
  return stateClass === stateFilter
}

/** 作业名 / CR / JobId / 提交人等关键字匹配 */
export function matchStreamJobKeyword(
  row: {
    name?: unknown
    id?: unknown
    flink_operator_deployment_name?: unknown
    flink_application_cluster_id?: unknown
    flink_job_id?: unknown
    last_submitted_by_username?: unknown
    pipeline_spec?: { source?: { topic?: unknown }; sink?: { table?: unknown } }
  },
  keyword: string,
): boolean {
  const kw = keyword.trim().toLowerCase()
  if (!kw) return true
  const hay = [
    row.name,
    row.id,
    row.flink_operator_deployment_name,
    row.flink_application_cluster_id,
    row.flink_job_id,
    row.last_submitted_by_username,
    row.pipeline_spec?.source?.topic,
    row.pipeline_spec?.sink?.table,
  ].filter(Boolean).join(' ').toLowerCase()
  return hay.includes(kw)
}

/** Operator vs Session / Application 部署模式（与 StreamMonitor.isOperatorJob 口径一致） */
export function isStreamOperatorJob(row: {
  job_type?: string
  flink_sql_submit_mode?: string | null
  flink_jar_submit_mode?: string | null
}): boolean {
  if (row.job_type === 'JAR') return (row.flink_jar_submit_mode || 'flink_operator') === 'flink_operator'
  if (row.job_type === 'SQL') return (row.flink_sql_submit_mode || 'flink_operator') === 'flink_operator'
  return false
}

export function streamJobDeployMode(row: {
  job_type?: string
  flink_sql_submit_mode?: string | null
  flink_jar_submit_mode?: string | null
}): string {
  if (isStreamOperatorJob(row)) return 'operator'
  return (row.flink_sql_submit_mode || row.flink_jar_submit_mode || 'session').toString().toLowerCase()
}

export function matchStreamJobDeployFilter(
  row: Parameters<typeof streamJobDeployMode>[0],
  deployFilter?: string | null,
): boolean {
  if (!deployFilter) return true
  return streamJobDeployMode(row) === deployFilter
}

export function matchStreamJobTypeFilter(
  jobType: string | undefined,
  typeFilter?: string | null,
): boolean {
  if (!typeFilter) return true
  return jobType === typeFilter
}

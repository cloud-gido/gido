/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 工作流发布预检：节点超时/重试摘要 + 覆盖提示（与后端 build_publish_precheck 对齐）。
 */
import { Alert } from 'antd'
import type { ReactNode } from 'react'

export type PublishPrecheckNode = {
  node_id?: number | null
  name?: string
  node_type?: string
  timeout_seconds?: number
  retry_times?: number
  retry_interval_minutes?: number
}

export type PublishPrecheck = {
  workflow_name?: string
  schedule_type?: string
  cron_expression?: string | null
  schedule?: {
    failure_strategy?: string
    process_priority?: string
    worker_group?: string
    timezone_id?: string
  }
  nodes?: PublishPrecheckNode[]
  node_count?: number
  overwrite_warning?: string
}

export function formatNodeRuntimeLine(n: PublishPrecheckNode): string {
  const name = n.name || `#${n.node_id ?? '?'}`
  const typ = n.node_type || '?'
  const timeout = n.timeout_seconds ?? 3600
  const retries = n.retry_times ?? 3
  const interval = n.retry_interval_minutes ?? 1
  return `${name}（${typ}）：超时 ${timeout}s / 重试 ${retries} 次 / 间隔 ${interval} 分`
}

export function renderPublishPrecheckBody(precheck: PublishPrecheck | null | undefined): ReactNode {
  if (!precheck) return null
  const sch = precheck.schedule || {}
  const nodes = Array.isArray(precheck.nodes) ? precheck.nodes : []
  return (
    <div>
      {precheck.overwrite_warning ? (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={precheck.overwrite_warning} />
      ) : null}
      <div style={{ fontSize: 13, marginBottom: 8, color: '#333' }}>
        失败策略 {sch.failure_strategy || 'CONTINUE'} · 优先级 {sch.process_priority || 'MEDIUM'} · Worker{' '}
        {sch.worker_group || 'default'} · 时区 {sch.timezone_id || 'Asia/Shanghai'}
        {precheck.schedule_type === 'cron'
          ? ` · Cron ${precheck.cron_expression || '—'}`
          : ' · 手动触发'}
      </div>
      <div style={{ maxHeight: 220, overflow: 'auto', fontSize: 12, color: '#555', lineHeight: 1.6 }}>
        {nodes.length === 0 ? (
          <div>DAG 暂无节点</div>
        ) : (
          nodes.map((n) => (
            <div key={n.node_id ?? n.name}>{formatNodeRuntimeLine(n)}</div>
          ))
        )}
      </div>
    </div>
  )
}

/** 编辑态本地摘要（未落库的 form + 当前 DAG 节点目录）。 */
export function buildLocalNodeRuntimeSummary(
  dagNodes: Array<{ node_id?: number }> | undefined,
  catalog: Array<{
    id: number
    name?: string
    node_type?: string
    timeout_seconds?: number
    retry_times?: number
    retry_interval_minutes?: number
  }>,
): PublishPrecheckNode[] {
  const ids = new Set(
    (dagNodes || [])
      .map((n) => Number(n.node_id))
      .filter((id) => Number.isFinite(id) && id > 0),
  )
  return catalog
    .filter((n) => ids.has(Number(n.id)))
    .map((n) => ({
      node_id: n.id,
      name: n.name,
      node_type: n.node_type,
      timeout_seconds: n.timeout_seconds ?? 3600,
      retry_times: n.retry_times ?? 3,
      retry_interval_minutes: n.retry_interval_minutes ?? 1,
    }))
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

export type InteractiveRunStatus =
  | 'idle'
  | 'starting'
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'timed_out'

export type InteractiveStatementStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'success'
  | 'failed'
  | 'skipped'
  | 'cancelled'

export interface InteractiveRun {
  id: number
  workspace_id: number
  source: 'studio' | 'probe'
  status: InteractiveRunStatus
  status_version?: number
  node_id?: number | null
  object_name?: string | null
  error_message?: string | null
  log_content?: string | null
  result_preview?: unknown
  started_at?: string | null
  finished_at?: string | null
  heartbeat_at?: string | null
  duration_ms?: number | null
  [key: string]: unknown
}

export interface InteractiveRunStatement {
  id: number
  run_id: number
  index: number
  statement_type?: string | null
  sql?: string | null
  status: InteractiveStatementStatus
  columns: string[]
  column_types: string[]
  fields?: InteractiveResultField[]
  execution_metrics?: InteractiveExecutionMetrics | null
  query_id?: string | null
  plan_snapshot?: unknown
  statement_version?: string | null
  affected_rows?: number | null
  total: number
  result_bytes: number
  chunk_count: number
  truncated: boolean
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface InteractiveResultField {
  name: string
  type?: string | null
  nullable?: boolean
  [key: string]: unknown
}

export interface InteractiveExecutionMetrics {
  execution_ms?: number | null
  fetch_ms?: number | null
  duration_ms?: number | null
  rows_returned?: number | null
  result_bytes?: number | null
  [key: string]: unknown
}

export type InteractiveRowsFilterOperator =
  | 'eq'
  | 'ne'
  | 'in'
  | 'contains'
  | 'starts_with'
  | 'gt'
  | 'gte'
  | 'lt'
  | 'lte'
  | 'is_null'
  | 'not_null'

export interface InteractiveRowsQueryRequest {
  search?: string
  filters?: Array<{ column: string; operator: InteractiveRowsFilterOperator; value?: unknown }>
  sort?: Array<{ column: string; direction: 'asc' | 'desc' }>
  cursor?: string | null
  limit?: number
  statement_version?: string | null
}

export interface InteractiveRowsQueryResponse {
  fields: InteractiveResultField[]
  rows: unknown[][]
  total: number
  source_total: number
  next_cursor: string | null
  has_more: boolean
  statement_version: string
  version_upgraded?: boolean
  statement_status?: string
  truncated?: boolean
}

export interface InteractiveStatementExplain {
  run_id: number
  statement_index: number
  plan_snapshot: unknown
  statement: InteractiveRunStatement
}

export interface InteractiveLogChunk {
  seq: number
  stream: string
  content: string
  created_at: string
}

export interface InteractiveRunEvents {
  run_id: number
  status: InteractiveRunStatus
  version: number
  timing?: {
    created_at?: string | null
    started_at?: string | null
    finished_at?: string | null
    queue_duration_ms?: number | null
    execution_duration_ms?: number | null
  }
  statement_version: string
  logs: {
    chunks: InteractiveLogChunk[]
    next_seq: number
    has_more: boolean
  }
  statements: InteractiveRunStatement[]
  statements_changed: boolean
  poll_required: boolean
}

export interface InteractiveStatementList {
  run_id: number
  status: InteractiveRunStatus
  version: number
  statement_version: string
  statements: InteractiveRunStatement[]
}

export interface InteractiveStatementRows {
  run_id: number
  run_status: InteractiveRunStatus
  run_version: number
  statement_id: number
  statement_index: number
  statement_status: InteractiveStatementStatus
  snapshot: boolean
  columns: string[]
  column_types: string[]
  rows: unknown[][]
  total: number
  truncated: boolean
  next_cursor: string | null
  has_more: boolean
}

export type InteractiveExportFormat = 'csv' | 'jsonl' | 'xlsx' | 'parquet'
export type InteractiveExportStatus =
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'expired'

export interface InteractiveRunExport {
  id: number
  run_id: number
  statement_id: number
  requested_by: number
  format: InteractiveExportFormat
  status: InteractiveExportStatus
  snapshot_scope: string
  row_count: number
  size_bytes: number
  file_name?: string | null
  error_message?: string | null
  expires_at?: string | null
  created_at: string
  finished_at?: string | null
  download_ready: boolean
}

export interface InteractiveRunShare {
  id: number
  run_id: number
  created_by: number
  expires_at: string
  revoked_at?: string | null
  revoked_by?: number | null
  created_at: string
  active: boolean
  token?: string
}

export interface InteractiveRunShareRedeem {
  share: InteractiveRunShare
  grant_id: number
  run: InteractiveRun
}

export interface InteractiveRunCapabilities {
  hasStatements: boolean
  hasResults: boolean
  canExport: boolean
  supportsMultipleStatements: boolean
}

export interface InteractiveRunState {
  runId: number | null
  status: InteractiveRunStatus
  version: number
  log: string
  error: string
  statements: InteractiveRunStatement[]
  statementVersion: string | null
  createdAt?: string | null
  startedAt?: string | null
  finishedAt?: string | null
  heartbeatAt?: string
  queueDurationMs?: number | null
  executionDurationMs?: number | null
}

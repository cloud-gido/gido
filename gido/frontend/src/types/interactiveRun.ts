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
  affected_rows?: number | null
  total: number
  result_bytes: number
  chunk_count: number
  truncated: boolean
  error?: string | null
  started_at?: string | null
  finished_at?: string | null
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

export type InteractiveExportFormat = 'csv' | 'jsonl'
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
  startedAt?: string
  heartbeatAt?: string
}

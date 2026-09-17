/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */

export type StatementPresentationKind = 'query' | 'dml' | 'ddl' | 'command'

export type StatementPresentationInput = {
  statement_type?: string | null
  sql?: string | null
  columns?: unknown[]
}

const DML = new Set(['INSERT', 'UPDATE', 'DELETE', 'MERGE', 'REPLACE', 'UPSERT', 'LOAD', 'COPY'])
const DDL = new Set(['CREATE', 'ALTER', 'DROP', 'TRUNCATE', 'RENAME', 'COMMENT'])
const QUERY = new Set(['SELECT', 'SHOW', 'DESC', 'DESCRIBE', 'EXPLAIN'])

export function statementPresentation(
  statement: StatementPresentationInput,
): { kind: StatementPresentationKind; type: string; tabLabel: string } {
  const type = String(statement.statement_type || '').trim().toUpperCase() || 'SQL'
  if ((statement.columns?.length ?? 0) > 0 || QUERY.has(type)) {
    return { kind: 'query', type, tabLabel: '结果' }
  }
  if (DML.has(type)) return { kind: 'dml', type, tabLabel: '变更' }
  if (DDL.has(type)) return { kind: 'ddl', type, tabLabel: 'DDL' }
  return { kind: 'command', type, tabLabel: '消息' }
}

export function statementObjectName(sql?: string | null): string | null {
  const match = String(sql || '').match(
    /\b(?:TABLE|VIEW|DATABASE|SCHEMA|INDEX)\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?([`"\[\]\w.-]+)/i,
  )
  return match?.[1]?.replace(/^[`"\[]|[`"\]]$/g, '') || null
}

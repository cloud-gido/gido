/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/** 将库类型名映射为表头右上角通用徽章（123 / abc 等，对标 DataGrip、Excel） */

export type ColumnTypeKind = 'number' | 'string' | 'datetime' | 'boolean' | 'json' | 'other'

export type ColumnTypeBadge = {
  kind: ColumnTypeKind
  badge: string
  title: string
}

/** 兼容历史接口里残留的 psycopg2 repr，如 "<psycopg2... 'INTEGER' at 0x...>" */
export function normalizeTypeLabel(typeLabel?: string): string {
  const raw = (typeLabel || '').trim()
  if (!raw) return ''
  if (!/psycopg/i.test(raw)) return raw
  const quoted = raw.match(/'([A-Z][A-Z0-9_]*)'/)
  if (quoted) {
    const key = quoted[1]
    const map: Record<string, string> = {
      LONGINTEGER: 'bigint',
      INTEGER: 'int',
      STRING: 'varchar',
      UNICODE: 'varchar',
      DATETIME: 'timestamp',
      DATETIMETZ: 'timestamptz',
      FLOAT: 'float',
      DECIMAL: 'decimal',
      BOOLEAN: 'bool',
      DATE: 'date',
      TIME: 'time',
    }
    return map[key] ?? key.toLowerCase()
  }
  return raw
}

export function classifyColumnType(typeLabel?: string): ColumnTypeBadge | null {
  const raw = normalizeTypeLabel(typeLabel)
  if (!raw) return null
  const t = raw.toLowerCase()

  if (/bool/.test(t)) {
    return { kind: 'boolean', badge: 'T/F', title: raw }
  }
  if (/json|jsonb/.test(t)) {
    return { kind: 'json', badge: '{}', title: raw }
  }
  if (
    /int|float|double|decimal|numeric|real|serial|bigint|smallint|tinyint|mediumint|money|number|longinteger/.test(
      t,
    )
  ) {
    return { kind: 'number', badge: '123', title: raw }
  }
  if (/timestamp|datetime|date|time|year/.test(t)) {
    return { kind: 'datetime', badge: 'dt', title: raw }
  }
  if (/char|text|varchar|string|uuid|enum|name|bpchar|clob/.test(t)) {
    return { kind: 'string', badge: 'abc', title: raw }
  }
  if (/bytea|blob|binary/.test(t)) {
    return { kind: 'other', badge: '01', title: raw }
  }
  return { kind: 'other', badge: '···', title: raw }
}

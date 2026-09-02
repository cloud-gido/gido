/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 只读 SQL 试跑 / 探查结果行数上限（与后端 sql_readonly / studio_sql_run / probe 一致）。
 */
export const SQL_RESULT_ROW_CAP = 10000

/** 数据探查新建查询默认最大行数（与数据开发试跑上限对齐） */
export const PROBE_DEFAULT_ROW_LIMIT = SQL_RESULT_ROW_CAP

export function clampSqlResultRowLimit(raw: unknown, fallback = SQL_RESULT_ROW_CAP): number {
  const n = Number(raw)
  if (!Number.isFinite(n)) return fallback
  return Math.min(Math.max(Math.trunc(n), 1), SQL_RESULT_ROW_CAP)
}

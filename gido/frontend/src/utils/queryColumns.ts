/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/** 查询结果列元数据（名称 + 库类型展示） */
export type QueryColumnMeta = { name: string; type?: string }

export function normalizeQueryColumns(
  columns: string[],
  columnTypes?: string[] | null,
): QueryColumnMeta[] {
  return columns.map((name, i) => ({
    name,
    type: columnTypes?.[i] || undefined,
  }))
}

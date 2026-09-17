/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export type QueryColumnMeta = {
  name: string
  type?: string
  raw_type?: string | null
  semantic_type?: string | null
  nullable?: boolean
  precision?: number | null
  scale?: number | null
}

export function normalizeQueryColumns(
  columns: string[],
  columnTypes?: string[] | null,
): QueryColumnMeta[] {
  return columns.map((name, i) => ({
    name,
    type: columnTypes?.[i] || undefined,
  }))
}

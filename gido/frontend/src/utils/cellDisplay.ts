/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/** 表格单元格统一展示（JSON/对象列与字典预览、查询结果一致） */
export function formatCellDisplay(v: unknown, maxLen = 240): string {
  if (v === null || v === undefined || v === 'None') return ''
  let s: string
  if (typeof v === 'object') {
    try {
      s = JSON.stringify(v)
    } catch {
      s = String(v)
    }
  } else {
    s = String(v)
  }
  if (maxLen > 0 && s.length > maxLen) return `${s.slice(0, maxLen)}…`
  return s
}

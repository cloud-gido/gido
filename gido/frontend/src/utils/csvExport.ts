/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
function escapeCell(v: unknown): string {
  if (v === null || v === undefined) return ''
  const s = String(v)
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`
  return s
}

export function exportRowsToCsv(columns: string[], rows: unknown[][], filename: string) {
  const header = columns.map(escapeCell).join(',')
  const body = rows.map(row => columns.map((_, i) => escapeCell((row as unknown[])[i])).join(',')).join('\r\n')
  const bom = '\uFEFF'
  const blob = new Blob([bom + header + '\r\n' + body], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

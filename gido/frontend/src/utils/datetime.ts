/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/**
 * 后端统一为 **naive UTC**（`datetime.utcnow()` 以及 Dolphin 墙钟时间在后端已换算为 UTC）。
 * JSON 多为无 Z 的 `2026-01-15 08:00:00`：按 UTC 解析后再用 `timeZone` 格式化为工作区展示。
 * 请勿对已是东八区墙钟但未换算的字符串再套本函数（否则会多 +8）。
 */
export function parseBackendUtcToDate(input: string | undefined | null): Date | null {
  if (input == null || String(input).trim() === '') return null
  const s = String(input).trim()
  if (s === 'Invalid Date') return null
  if (/[zZ]$/.test(s) || /[+-]\d{2}:?\d{2}$/.test(s)) {
    const d = new Date(s)
    return Number.isNaN(d.getTime()) ? null : d
  }
  const normalized = s.includes('T') ? s : s.replace(' ', 'T')
  const d = new Date(`${normalized}Z`)
  return Number.isNaN(d.getTime()) ? null : d
}

export function formatInTimeZone(
  input: string | undefined | null,
  timeZone: string,
  empty = '—'
): string {
  const d = parseBackendUtcToDate(input)
  if (!d) return empty
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: timeZone || 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(d)
  } catch {
    return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z'
  }
}

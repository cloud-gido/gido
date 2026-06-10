/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export function mergeColumnOrderWithKeys(order: string[] | undefined | null, keys: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const k of order ?? []) {
    if (keys.includes(k) && !seen.has(k)) {
      out.push(k)
      seen.add(k)
    }
  }
  for (const k of keys) {
    if (!seen.has(k)) {
      out.push(k)
      seen.add(k)
    }
  }
  return out
}

export function pruneWidths(widths: Record<string, number>, keys: string[]): Record<string, number> {
  const next: Record<string, number> = {}
  for (const k of keys) {
    if (widths[k] != null) next[k] = widths[k]
  }
  return next
}

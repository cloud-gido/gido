/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */

/** 列名序列签名：用于判断是否为同一次结果的列集合（同名同序） */
export function columnKeysSignature(keys: string[] | undefined | null): string {
  return (keys ?? []).join('\x1e')
}

/**
 * 合并用户拖拽顺序与当前结果列：saved order 优先，缺失列按 keys 追加。
 * 仅应在「同一结果列签名」下使用；跨查询请用 resolveResultColumnOrder。
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

/**
 * 查询结果列展示顺序：
 * - 若本次结果的列名序列与产生 savedOrder 时一致（同一 SELECT 结果），保留用户拖拽顺序
 * - 否则以本次 API/SQL 返回的 keys 为准（避免旧缓存盖住 SELECT 列序）
 */
export function resolveResultColumnOrder(
  savedOrder: string[] | undefined | null,
  keys: string[],
  sourceKeys?: string[] | null,
): string[] {
  if (!keys.length) return []
  if (
    sourceKeys?.length &&
    columnKeysSignature(sourceKeys) === columnKeysSignature(keys) &&
    (savedOrder?.length ?? 0) > 0
  ) {
    return mergeColumnOrderWithKeys(savedOrder, keys)
  }
  return [...keys]
}

export function pruneWidths(widths: Record<string, number>, keys: string[]): Record<string, number> {
  const next: Record<string, number> = {}
  for (const k of keys) {
    if (widths[k] != null) next[k] = widths[k]
  }
  return next
}

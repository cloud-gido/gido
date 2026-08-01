/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树排序：默认名称字典序；sort_order>0 表示用户拖拽后的手工序。
 */

function cmpId(a: string | number | null | undefined, b: string | number | null | undefined): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isFinite(na) && Number.isFinite(nb) && String(a) === String(na) && String(b) === String(nb)) {
    return na - nb
  }
  return String(a ?? '').localeCompare(String(b ?? ''), 'zh-CN', { numeric: true, sensitivity: 'base' })
}

export function cmpZhName(a: string, b: string): number {
  return String(a || '').localeCompare(String(b || ''), 'zh-CN', {
    numeric: true,
    sensitivity: 'base',
  })
}

/** 叶子 / 目录：先 sort_order，同序再按名称，再 id */
export function sortLeavesByOrderThenName<
  T extends { id?: string | number; name?: string; sort_order?: number | null },
>(list: T[]): T[] {
  return [...list].sort((a, b) => {
    const so = (a.sort_order ?? 0) - (b.sort_order ?? 0)
    if (so !== 0) return so
    const nc = cmpZhName(a.name || '', b.name || '')
    if (nc !== 0) return nc
    return cmpId(a.id, b.id)
  })
}

/** @deprecated 使用 sortLeavesByOrderThenName；保留别名兼容 */
export const sortFoldersByOrderThenName = sortLeavesByOrderThenName

/** 目录：按名称字典序（无 sort_order 时） */
export function sortFoldersByName<T extends { id?: string | number; name?: string }>(list: T[]): T[] {
  return [...list].sort((a, b) => {
    const nc = cmpZhName(a.name || '', b.name || '')
    if (nc !== 0) return nc
    return cmpId(a.id, b.id)
  })
}

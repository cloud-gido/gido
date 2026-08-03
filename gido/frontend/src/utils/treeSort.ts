/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 目录树排序（对齐常见操作系统 / IDEA 资源管理器）：
 * 同级固定「目录在前、脚本在后」，组内按名称字典序；不支持手工拖拽排序。
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

/** 按名称字典序（zh-CN + numeric）；同名再按 id。忽略 sort_order。 */
export function sortByName<
  T extends { id?: string | number; name?: string },
>(list: T[]): T[] {
  return [...list].sort((a, b) => {
    const nc = cmpZhName(a.name || '', b.name || '')
    if (nc !== 0) return nc
    return cmpId(a.id, b.id)
  })
}

/** @deprecated 使用 sortByName；保留别名兼容旧调用 */
export const sortLeavesByOrderThenName = sortByName
/** @deprecated 使用 sortByName */
export const sortFoldersByOrderThenName = sortByName
/** @deprecated 使用 sortByName */
export const sortFoldersByName = sortByName

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图收藏与最近浏览。只存在本机，不进字典。
 */

export type DataMapShortcut = {
  datasourceId: number
  catalog: string
  tableName: string
  comment?: string
  metaTableId?: number
}

const MAX_PINS = 30
const MAX_RECENT = 8

function storageKey(wsId: number, kind: 'pins' | 'recent') {
  return `gido.datamap.${kind}.${wsId}`
}

function sameTable(a: DataMapShortcut, b: DataMapShortcut) {
  return a.datasourceId === b.datasourceId
    && a.catalog === b.catalog
    && a.tableName === b.tableName
}

export function readShortcuts(wsId: number | undefined, kind: 'pins' | 'recent'): DataMapShortcut[] {
  if (!wsId) return []
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey(wsId, kind)) || '[]')
    if (!Array.isArray(raw)) return []
    return raw.filter((item) => item && item.tableName && item.datasourceId)
  } catch {
    return []
  }
}

function writeShortcuts(wsId: number, kind: 'pins' | 'recent', items: DataMapShortcut[]) {
  localStorage.setItem(storageKey(wsId, kind), JSON.stringify(items))
}

export function togglePin(wsId: number, pin: DataMapShortcut): DataMapShortcut[] {
  const current = readShortcuts(wsId, 'pins')
  const next = current.some((item) => sameTable(item, pin))
    ? current.filter((item) => !sameTable(item, pin))
    : [pin, ...current].slice(0, MAX_PINS)
  writeShortcuts(wsId, 'pins', next)
  return next
}

export function pushRecent(wsId: number, pin: DataMapShortcut): DataMapShortcut[] {
  const next = [pin, ...readShortcuts(wsId, 'recent').filter((item) => !sameTable(item, pin))].slice(0, MAX_RECENT)
  writeShortcuts(wsId, 'recent', next)
  return next
}

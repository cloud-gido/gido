/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图「在探查中打开」交给数据探查的一次性脚本，不改已有查询。
 */

const KEY = 'gido.probe.pendingScript'

export type PendingProbeScript = {
  name: string
  sql: string
  datasourceId?: number
}

export function queuePendingProbeScript(payload: PendingProbeScript) {
  sessionStorage.setItem(KEY, JSON.stringify({
    name: (payload.name || '数据地图查询').slice(0, 64),
    sql: payload.sql,
    datasourceId: payload.datasourceId,
  }))
}

export function takePendingProbeScript(): PendingProbeScript | null {
  const raw = sessionStorage.getItem(KEY)
  if (!raw) return null
  sessionStorage.removeItem(KEY)
  try {
    const parsed = JSON.parse(raw) as PendingProbeScript
    if (!parsed?.sql || typeof parsed.sql !== 'string') return null
    return parsed
  } catch {
    return null
  }
}

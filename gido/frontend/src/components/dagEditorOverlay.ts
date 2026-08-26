/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * DAG 编排全屏 / 浮层叠层与布局纯函数。
 * 抽出以便单测，避免挂载 AntV X6。
 *
 * 叠层约定（Ant Design Modal 默认约 1000）：
 * - 全屏壳须盖住编辑工作流 Modal
 * - Select / Tooltip 等须高于全屏壳，否则挂 body 时「看不见下拉」
 * - 节点全名 tip / NodeConfigModal 再高于脚本下拉
 * - 抢锁 confirm 最高
 */
export const Z_FULLSCREEN = 2100
export const Z_POPUP = 2200
export const Z_NODE_TIP = 2300
export const Z_NODE_CONFIG = 2300
export const Z_NODE_CONFIG_CONFIRM = 2400

/** 全屏时 Select/Tooltip 显式 zIndex；小窗沿用 antd 默认即可 */
export function dagPopupZIndex(fullscreen: boolean): number | undefined {
  return fullscreen ? Z_POPUP : undefined
}

/** 全屏时抬高 ConfigProvider zIndexPopupBase */
export function dagPopupBase(fullscreen: boolean): number | undefined {
  return fullscreen ? Z_POPUP : undefined
}

export function filterPublishedScriptOption(input: string, scriptName: string): boolean {
  const q = (input || '').trim().toLowerCase()
  if (!q) return true
  return String(scriptName || '').toLowerCase().includes(q)
}

const GAP_X = 200
const GAP_Y = 100
const ORIGIN_X = 80
const ORIGIN_Y = 60

/** 按依赖分层从左到右排布（拓扑层），同层纵向排列 */
export function computeLayeredLayout(
  nodeIds: number[],
  edges: { source: number; target: number }[],
): Map<number, { x: number; y: number }> {
  const idSet = new Set(nodeIds)
  const indeg = new Map<number, number>()
  const outs = new Map<number, number[]>()
  for (const id of nodeIds) {
    indeg.set(id, 0)
    outs.set(id, [])
  }
  for (const e of edges) {
    if (!idSet.has(e.source) || !idSet.has(e.target) || e.source === e.target) continue
    outs.get(e.source)!.push(e.target)
    indeg.set(e.target, (indeg.get(e.target) || 0) + 1)
  }

  const layerOf = new Map<number, number>()
  const queue = nodeIds.filter(id => (indeg.get(id) || 0) === 0)
  for (const id of queue) layerOf.set(id, 0)

  const q = [...queue]
  while (q.length) {
    const u = q.shift()!
    const lu = layerOf.get(u) || 0
    for (const v of outs.get(u) || []) {
      const next = lu + 1
      if (!layerOf.has(v) || (layerOf.get(v) || 0) < next) {
        layerOf.set(v, next)
      }
      const d = (indeg.get(v) || 0) - 1
      indeg.set(v, d)
      if (d === 0) q.push(v)
    }
  }
  for (const id of nodeIds) {
    if (!layerOf.has(id)) layerOf.set(id, 0)
  }

  const layers = new Map<number, number[]>()
  for (const id of nodeIds) {
    const L = layerOf.get(id) || 0
    if (!layers.has(L)) layers.set(L, [])
    layers.get(L)!.push(id)
  }
  for (const ids of layers.values()) ids.sort((a, b) => a - b)

  const pos = new Map<number, { x: number; y: number }>()
  const sortedLayers = [...layers.keys()].sort((a, b) => a - b)
  for (const L of sortedLayers) {
    const ids = layers.get(L) || []
    ids.forEach((id, i) => {
      pos.set(id, { x: ORIGIN_X + L * GAP_X, y: ORIGIN_Y + i * GAP_Y })
    })
  }
  return pos
}

/** 回归：叠层数值关系（全屏壳 < 浮层 ≤ tip/配置 < confirm） */
export function assertDagOverlayStackOrder(): void {
  if (!(Z_FULLSCREEN > 1000)) throw new Error('fullscreen must cover antd Modal(~1000)')
  if (!(Z_POPUP > Z_FULLSCREEN)) throw new Error('popup must cover fullscreen shell')
  if (!(Z_NODE_TIP >= Z_POPUP)) throw new Error('node tip must be above or equal popup')
  if (!(Z_NODE_CONFIG >= Z_POPUP)) throw new Error('node config must cover popup')
  if (!(Z_NODE_CONFIG_CONFIRM > Z_NODE_CONFIG)) throw new Error('confirm must cover node config')
}

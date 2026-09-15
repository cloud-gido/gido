/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 列宽拖拽结束后短暂抑制表头 click / sorter，避免「松手即排序」。
 * 模块级短窗口即可，无需 Context，打开成本为零。
 */
let suppressUntil = 0

/** 拖列宽过程中或松手时调用 */
export function markColumnResizeGesture(holdMs = 400): void {
  suppressUntil = Math.max(suppressUntil, Date.now() + holdMs)
}

/** 表头排序 / onChange 前调用：刚拖过列宽则忽略本次排序意图 */
export function shouldSuppressHeaderInteraction(): boolean {
  return Date.now() < suppressUntil
}

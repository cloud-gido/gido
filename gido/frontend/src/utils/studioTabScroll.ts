/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Tab 条横向滚动：只改容器 scrollLeft，不用 Element.scrollIntoView
 *（后者会带动祖先滚动 / 在部分浏览器近似居中，造成「跳回中间、看不到文件头」）。
 */

/** 将 child 完整滚入 container 可视区；已完整可见则不动。优先露出左侧（文件名开头）。 */
export function ensureChildFullyVisibleHorizontally(
  container: HTMLElement,
  child: HTMLElement,
  paddingPx = 8,
): void {
  const cRect = container.getBoundingClientRect()
  const tRect = child.getBoundingClientRect()
  const leftOverflow = cRect.left + paddingPx - tRect.left
  const rightOverflow = tRect.right - (cRect.right - paddingPx)
  if (leftOverflow > 0) {
    container.scrollLeft -= leftOverflow
  } else if (rightOverflow > 0) {
    container.scrollLeft += rightOverflow
  }
}

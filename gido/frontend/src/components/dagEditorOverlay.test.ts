/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * DAG 全屏叠层 / 布局 / 脚本过滤：单元测试。
 */
import { describe, expect, it } from 'vitest'
import {
  Z_FULLSCREEN,
  Z_NODE_CONFIG,
  Z_NODE_CONFIG_CONFIRM,
  Z_NODE_TIP,
  Z_POPUP,
  assertDagOverlayStackOrder,
  computeLayeredLayout,
  dagPopupBase,
  dagPopupZIndex,
  filterPublishedScriptOption,
} from './dagEditorOverlay'

describe('dagEditorOverlay z-index stack', () => {
  it('keeps fullscreen above antd Modal and popups above fullscreen', () => {
    expect(Z_FULLSCREEN).toBeGreaterThan(1000)
    expect(Z_POPUP).toBeGreaterThan(Z_FULLSCREEN)
    expect(Z_NODE_TIP).toBeGreaterThanOrEqual(Z_POPUP)
    expect(Z_NODE_CONFIG).toBeGreaterThanOrEqual(Z_POPUP)
    expect(Z_NODE_CONFIG_CONFIRM).toBeGreaterThan(Z_NODE_CONFIG)
    expect(() => assertDagOverlayStackOrder()).not.toThrow()
  })

  it('raises popup z-index only in fullscreen', () => {
    expect(dagPopupZIndex(false)).toBeUndefined()
    expect(dagPopupZIndex(true)).toBe(Z_POPUP)
    expect(dagPopupBase(false)).toBeUndefined()
    expect(dagPopupBase(true)).toBe(Z_POPUP)
  })
})

describe('filterPublishedScriptOption', () => {
  it('matches by case-insensitive substring and treats empty query as all', () => {
    expect(filterPublishedScriptOption('', 'import_dim_goods')).toBe(true)
    expect(filterPublishedScriptOption('  ', 'import_dim_goods')).toBe(true)
    expect(filterPublishedScriptOption('DIM', 'import_dim_goods')).toBe(true)
    expect(filterPublishedScriptOption('xyz', 'import_dim_goods')).toBe(false)
  })
})

describe('computeLayeredLayout', () => {
  it('places dependency chain left-to-right by layer', () => {
    const pos = computeLayeredLayout(
      [1, 2, 3],
      [
        { source: 1, target: 2 },
        { source: 2, target: 3 },
      ],
    )
    expect(pos.get(1)!.x).toBeLessThan(pos.get(2)!.x)
    expect(pos.get(2)!.x).toBeLessThan(pos.get(3)!.x)
  })

  it('stacks same-layer nodes vertically', () => {
    const pos = computeLayeredLayout(
      [1, 2, 3],
      [
        { source: 1, target: 2 },
        { source: 1, target: 3 },
      ],
    )
    expect(pos.get(2)!.x).toBe(pos.get(3)!.x)
    expect(pos.get(2)!.y).not.toBe(pos.get(3)!.y)
  })
})

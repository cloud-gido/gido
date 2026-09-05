/**
 * @vitest-environment jsdom
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { ensureChildFullyVisibleHorizontally } from './studioTabScroll'

function mockRect(el: HTMLElement, rect: Partial<DOMRect>) {
  el.getBoundingClientRect = () => ({
    x: rect.left ?? 0,
    y: rect.top ?? 0,
    top: rect.top ?? 0,
    left: rect.left ?? 0,
    bottom: rect.bottom ?? 0,
    right: rect.right ?? 0,
    width: rect.width ?? 0,
    height: rect.height ?? 0,
    toJSON: () => ({}),
  })
}

describe('ensureChildFullyVisibleHorizontally', () => {
  it('does not move scroll when child is fully visible', () => {
    const container = document.createElement('div')
    const child = document.createElement('div')
    container.scrollLeft = 40
    mockRect(container, { left: 100, right: 300 })
    mockRect(child, { left: 140, right: 220 })
    ensureChildFullyVisibleHorizontally(container, child, 0)
    expect(container.scrollLeft).toBe(40)
  })

  it('scrolls left to reveal child start (file name head)', () => {
    const container = document.createElement('div')
    const child = document.createElement('div')
    container.scrollLeft = 80
    mockRect(container, { left: 100, right: 300 })
    mockRect(child, { left: 60, right: 160 })
    ensureChildFullyVisibleHorizontally(container, child, 0)
    expect(container.scrollLeft).toBe(40)
  })

  it('scrolls right only enough to reveal child end (not center)', () => {
    const container = document.createElement('div')
    const child = document.createElement('div')
    container.scrollLeft = 10
    mockRect(container, { left: 100, right: 300 })
    mockRect(child, { left: 250, right: 360 })
    ensureChildFullyVisibleHorizontally(container, child, 0)
    expect(container.scrollLeft).toBe(70)
  })
})

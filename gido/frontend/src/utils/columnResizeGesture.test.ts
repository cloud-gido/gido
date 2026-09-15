/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { markColumnResizeGesture, shouldSuppressHeaderInteraction } from './columnResizeGesture'

describe('columnResizeGesture', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('suppresses header interaction only within the hold window', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    expect(shouldSuppressHeaderInteraction()).toBe(false)

    markColumnResizeGesture(400)
    expect(shouldSuppressHeaderInteraction()).toBe(true)

    vi.setSystemTime(1_000_350)
    expect(shouldSuppressHeaderInteraction()).toBe(true)

    vi.setSystemTime(1_000_450)
    expect(shouldSuppressHeaderInteraction()).toBe(false)
  })

  it('extends suppressUntil when a longer window is marked', () => {
    vi.useFakeTimers()
    vi.setSystemTime(2_000_000)
    markColumnResizeGesture(100)
    markColumnResizeGesture(500)
    vi.setSystemTime(2_000_200)
    expect(shouldSuppressHeaderInteraction()).toBe(true)
    vi.setSystemTime(2_000_600)
    expect(shouldSuppressHeaderInteraction()).toBe(false)
  })
})

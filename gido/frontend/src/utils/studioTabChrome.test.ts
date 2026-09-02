/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  planStudioSessionTabOrder,
  resolveStudioTabChrome,
  studioTabTitleColor,
  studioTabTitleItalic,
} from './studioTabChrome'

describe('resolveStudioTabChrome', () => {
  it('orders loading > error > pending > ready', () => {
    expect(resolveStudioTabChrome({ script_content: null, loading: true, error: 'x' })).toBe('loading')
    expect(resolveStudioTabChrome({ script_content: null, error: 'fail' })).toBe('error')
    expect(resolveStudioTabChrome({ script_content: null })).toBe('pending')
    expect(resolveStudioTabChrome({ script_content: 'SELECT 1' })).toBe('ready')
  })
})

describe('studioTabTitle*', () => {
  it('uses italic for non-ready chrome', () => {
    expect(studioTabTitleItalic('pending')).toBe(true)
    expect(studioTabTitleItalic('loading')).toBe(true)
    expect(studioTabTitleItalic('error')).toBe(true)
    expect(studioTabTitleItalic('ready')).toBe(false)
  })

  it('uses amber for error tabs', () => {
    expect(studioTabTitleColor('error', true)).toBe('#d48806')
    expect(studioTabTitleColor('ready', true)).toBe('#1677ff')
  })
})

describe('planStudioSessionTabOrder', () => {
  it('keeps session order when prefer already present', () => {
    expect(planStudioSessionTabOrder([1, 2, 3], 2)).toEqual({
      tabIds: [1, 2, 3],
      activeId: 2,
    })
  })

  it('prepends prefer when not in session', () => {
    expect(planStudioSessionTabOrder([2, 3], 9)).toEqual({
      tabIds: [9, 2, 3],
      activeId: 9,
    })
  })
})

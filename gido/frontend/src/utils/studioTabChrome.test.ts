/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  canRunStudioTabShortcut,
  mergeStudioSessionTabOrder,
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

describe('mergeStudioSessionTabOrder', () => {
  it('keeps stored tabs when user opens a new active tab before restore', () => {
    expect(mergeStudioSessionTabOrder([2, 3], [9], 9)).toEqual({
      tabIds: [9, 2, 3],
      activeId: 9,
    })
  })

  it('preserves the stored order for an already restored active tab', () => {
    expect(mergeStudioSessionTabOrder([1, 2, 3], [2], 2)).toEqual({
      tabIds: [1, 2, 3],
      activeId: 2,
    })
  })
})

describe('canRunStudioTabShortcut', () => {
  const readySql = { node_type: 'SQL', script_content: 'SELECT 1', is_locked: false }

  it('allows ready SQL/PYTHON tabs with run permission', () => {
    expect(canRunStudioTabShortcut({ canRun: true, node: readySql })).toBe(true)
    expect(canRunStudioTabShortcut({
      canRun: true,
      node: { ...readySql, node_type: 'PYTHON' },
    })).toBe(true)
  })

  it('blocks pending, error, locked and running tabs', () => {
    expect(canRunStudioTabShortcut({
      canRun: true,
      node: { ...readySql, script_content: null },
    })).toBe(false)
    expect(canRunStudioTabShortcut({ canRun: true, node: readySql, error: 'load failed' })).toBe(false)
    expect(canRunStudioTabShortcut({
      canRun: true,
      node: { ...readySql, is_locked: true },
    })).toBe(false)
    expect(canRunStudioTabShortcut({ canRun: true, node: readySql, running: true })).toBe(false)
  })
})

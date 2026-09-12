/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  buildWorkspaceSelectOptions,
  workspaceSelectDisplayLabel,
} from './workspaceSelectOptions'

describe('workspaceSelectOptions', () => {
  it('keeps current workspace label when list is still empty', () => {
    const opts = buildWorkspaceSelectOptions([], { id: 7, name: 'infras', my_role: 'admin' })
    expect(opts).toEqual([{ value: 7, label: 'infras · 空间管理员' }])
  })

  it('does not flash bare id when options briefly empty', () => {
    expect(workspaceSelectDisplayLabel(7, [], { id: 7, name: 'infras' })).toBe('infras')
    expect(workspaceSelectDisplayLabel(7, [{ value: 7, label: 'infras' }], null)).toBe('infras')
  })

  it('merges current into existing list without duplicating', () => {
    const opts = buildWorkspaceSelectOptions(
      [{ id: 7, name: 'infras' }, { id: 8, name: 'ads' }],
      { id: 7, name: 'infras' },
    )
    expect(opts.map(o => o.value)).toEqual([7, 8])
  })
})

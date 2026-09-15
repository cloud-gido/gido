/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  capSuggestions,
  completionSortText,
  filterByPrefixRanked,
  formatColumnDetail,
} from './sqlCompletionRank'

describe('sqlCompletionRank', () => {
  it('ranks startsWith ahead of includes via sortText', () => {
    const a = completionSortText({ tier: 'col', name: 'user_id', prefix: 'user' })
    const b = completionSortText({ tier: 'col', name: 'x_user', prefix: 'user' })
    expect(a < b).toBe(true)
  })

  it('boosts recent items', () => {
    const recent = completionSortText({ tier: 'table', name: 'ads_foo', prefix: '', recent: true })
    const plain = completionSortText({ tier: 'table', name: 'ads_foo', prefix: '', recent: false })
    expect(recent < plain).toBe(true)
  })

  it('formats column detail with PK / type / NOT NULL', () => {
    expect(formatColumnDetail({
      type: 'bigint',
      key: 'PRI',
      nullable: false,
      comment: '用户ID',
    })).toContain('PK')
    expect(formatColumnDetail({
      type: 'bigint',
      key: 'PRI',
      nullable: false,
      comment: '用户ID',
    })).toContain('bigint')
    expect(formatColumnDetail({
      type: 'bigint',
      key: 'PRI',
      nullable: false,
      comment: '用户ID',
    })).toContain('NOT NULL')
  })

  it('filterByPrefixRanked prefers prefix starts', () => {
    const ranked = filterByPrefixRanked(
      [{ name: 'x_order' }, { name: 'order_id' }, { name: 'border' }],
      'order',
    ).map(x => x.name)
    expect(ranked[0]).toBe('order_id')
    expect(ranked).toContain('x_order')
  })

  it('capSuggestions soft-caps without prefix', () => {
    const items = Array.from({ length: 200 }, (_, i) => ({ i }))
    expect(capSuggestions(items, '', 50, 100)).toHaveLength(50)
    expect(capSuggestions(items, 'a', 50, 100)).toHaveLength(100)
  })
})

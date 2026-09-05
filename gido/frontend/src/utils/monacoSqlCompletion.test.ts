/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @vitest-environment jsdom
 */
import { describe, expect, it } from 'vitest'
import { parseSqlCompletionContext, textBeforeCursorOnLine } from './monacoSqlCompletion'

describe('parseSqlCompletionContext', () => {
  it('parses bare prefix', () => {
    expect(parseSqlCompletionContext('SELECT * FROM ads_')).toEqual({
      kind: 'bare',
      prefix: 'ads_',
    })
  })

  it('parses catalog.dot for tables', () => {
    expect(parseSqlCompletionContext('FROM bigdata_ads.')).toEqual({
      kind: 'after_dot',
      left: 'bigdata_ads',
      prefix: '',
    })
    expect(parseSqlCompletionContext('FROM bigdata_ads.ads_g')).toEqual({
      kind: 'after_dot',
      left: 'bigdata_ads',
      prefix: 'ads_g',
    })
  })

  it('parses catalog.table. for columns', () => {
    expect(parseSqlCompletionContext('SELECT bigdata_ads.ads_foo.')).toEqual({
      kind: 'column_qualified',
      catalog: 'bigdata_ads',
      table: 'ads_foo',
      prefix: '',
    })
  })

  it('textBeforeCursorOnLine respects column', () => {
    expect(textBeforeCursorOnLine('abcdef', 4)).toBe('abc')
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @vitest-environment jsdom
 */
import { describe, expect, it } from 'vitest'
import {
  currentSqlStatement,
  detectSqlSuggestSlot,
  enclosingSqlScopes,
  extractCteDefinitions,
  extractSelectListColumns,
  extractTableRefs,
  parseSqlCompletionContext,
  resolveScopedTableRefs,
  stripSqlNoise,
  textBeforeCursorOnLine,
} from './monacoSqlCompletion'

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

describe('detectSqlSuggestSlot', () => {
  it('SELECT space is column_slot not table_slot', () => {
    expect(detectSqlSuggestSlot('SELECT ')).toBe('column_slot')
    expect(detectSqlSuggestSlot('SELECT')).toBe('column_slot')
    expect(detectSqlSuggestSlot('select col_')).toBe('column_slot')
  })

  it('FROM / JOIN are table_slot', () => {
    expect(detectSqlSuggestSlot('FROM ')).toBe('table_slot')
    expect(detectSqlSuggestSlot('SELECT * FROM ')).toBe('table_slot')
    expect(detectSqlSuggestSlot('FROM ads_')).toBe('table_slot')
    expect(detectSqlSuggestSlot('LEFT JOIN ')).toBe('table_slot')
    expect(detectSqlSuggestSlot('INNER JOIN bigdata_')).toBe('table_slot')
  })

  it('FROM ( starts subquery without table flood', () => {
    expect(detectSqlSuggestSlot('FROM (')).toBe('keyword_only')
    expect(detectSqlSuggestSlot('JOIN (')).toBe('keyword_only')
  })

  it('WHERE / ORDER BY are column_slot', () => {
    expect(detectSqlSuggestSlot('FROM t WHERE ')).toBe('column_slot')
    expect(detectSqlSuggestSlot('ORDER BY ')).toBe('column_slot')
    expect(detectSqlSuggestSlot('GROUP BY ')).toBe('column_slot')
  })

  it('statement start is keyword_only', () => {
    expect(detectSqlSuggestSlot('')).toBe('keyword_only')
    expect(detectSqlSuggestSlot('  ')).toBe('keyword_only')
  })
})

describe('extractTableRefs', () => {
  it('extracts FROM catalog.table alias and JOIN', () => {
    const refs = extractTableRefs(
      'SELECT a.id FROM bigdata_ads.ads_foo a JOIN dim_bar b ON a.id = b.id',
    )
    expect(refs).toEqual(expect.arrayContaining([
      expect.objectContaining({ catalog: 'bigdata_ads', table: 'ads_foo', alias: 'a' }),
      expect.objectContaining({ catalog: null, table: 'dim_bar', alias: 'b' }),
    ]))
  })

  it('extracts bare table without alias', () => {
    expect(extractTableRefs('select * from ads_order')).toEqual([
      expect.objectContaining({ catalog: null, table: 'ads_order', alias: null }),
    ])
  })

  it('does not treat WHERE as alias', () => {
    expect(extractTableRefs('FROM ads_foo WHERE id = 1')).toEqual([
      expect.objectContaining({ catalog: null, table: 'ads_foo', alias: null }),
    ])
  })

  it('extracts subquery alias and projected columns', () => {
    const refs = extractTableRefs(
      'SELECT * FROM (SELECT user_id AS uid, amt FROM ads_order) s',
    )
    expect(refs).toEqual(expect.arrayContaining([
      expect.objectContaining({
        kind: 'subquery',
        alias: 's',
        columns: expect.arrayContaining(['uid', 'amt']),
      }),
    ]))
  })
})

describe('CTE and scopes', () => {
  it('stripSqlNoise removes line comments and strings', () => {
    const raw = "SELECT 'a;b' -- comment\nFROM t"
    const cleaned = stripSqlNoise(raw)
    expect(cleaned).not.toContain('comment')
    expect(cleaned.indexOf(';')).toBe(-1)
    expect(cleaned).toContain('FROM')
  })

  it('extractCteDefinitions parses WITH bodies and select list', () => {
    const sql = `
      WITH orders AS (
        SELECT user_id, amount AS amt FROM ads_order
      ),
      users AS (
        SELECT id FROM dim_user
      )
      SELECT * FROM orders
    `
    const ctes = extractCteDefinitions(sql)
    expect(ctes.map(c => c.name)).toEqual(['orders', 'users'])
    expect(ctes[0].columns).toEqual(expect.arrayContaining(['user_id', 'amt']))
  })

  it('resolveScopedTableRefs maps CTE in FROM', () => {
    const sql = 'WITH orders AS (SELECT user_id FROM ads_order) SELECT  FROM orders'
    const offset = sql.indexOf('SELECT ') + 'SELECT '.length
    const { refs, ctes } = resolveScopedTableRefs(sql, offset)
    expect(ctes.map(c => c.name)).toContain('orders')
    expect(refs.some(r => r.kind === 'cte' && r.table === 'orders')).toBe(true)
  })

  it('enclosingSqlScopes prefers innermost subquery', () => {
    const sql = 'SELECT * FROM ads_outer WHERE id IN (SELECT x FROM ads_inner WHERE '
    const offset = sql.length
    const scopes = enclosingSqlScopes(sql, offset)
    expect(scopes[0]).toContain('ads_inner')
    expect(scopes[0]).not.toContain('ads_outer')
    expect(scopes[scopes.length - 1]).toContain('ads_outer')
  })

  it('extractSelectListColumns reads aliases', () => {
    expect(extractSelectListColumns('SELECT a, b AS bb, t.c FROM x')).toEqual(
      expect.arrayContaining(['a', 'bb', 'c']),
    )
  })
})

describe('currentSqlStatement', () => {
  it('scopes by semicolon and keeps text after cursor for lookahead', () => {
    const sql = 'SELECT x FROM a; SELECT y\nFROM b WHERE '
    const offset = sql.indexOf('SELECT y') + 'SELECT y'.length
    const { text, beforeCursor } = currentSqlStatement(sql, offset)
    expect(text.trim().startsWith('SELECT y')).toBe(true)
    expect(text).toContain('FROM b')
    expect(beforeCursor.trim()).toBe('SELECT y')
  })
})

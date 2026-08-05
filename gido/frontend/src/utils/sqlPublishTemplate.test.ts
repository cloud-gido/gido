/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { buildDefaultSqlPublishScript } from './sqlPublishTemplate'

describe('buildDefaultSqlPublishScript', () => {
  it('fills name author dates and uses only line comments', () => {
    const sql = buildDefaultSqlPublishScript({
      scriptName: 'dwd_gameline_bet_combination',
      author: 'felixzhu',
      createdAt: '2026-08-05',
    })
    expect(sql).toContain('-- 脚本名称 : dwd_gameline_bet_combination')
    expect(sql).toContain('-- Job / 节点 : dwd_gameline_bet_combination')
    expect(sql).toContain('-- 作者       : felixzhu')
    expect(sql).toContain('-- 创建时间   : 2026-08-05')
    expect(sql).toContain('-- 更新时间   : 2026-08-05')
    expect(sql).toContain('USE <target_db>;')
    expect(sql).toContain('SELECT 1 AS ok;')
    expect(sql).not.toMatch(/\/\*/)
    expect(sql).not.toMatch(/\*\//)
  })

  it('keeps placeholders when name or author missing', () => {
    const sql = buildDefaultSqlPublishScript({ createdAt: '2026-01-01' })
    expect(sql).toContain('-- 脚本名称 : <脚本名称>')
    expect(sql).toContain('-- 作者       : <作者>')
  })
})

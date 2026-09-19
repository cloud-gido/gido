/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图最终形态：左树（sqlSchemaCache）+ 右详；打开即 ensure；404 回退 register。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DataMap catalog tree adoption', () => {
  it('uses left tree + right detail with sqlSchemaCache', () => {
    const page = read('pages/DataMap.tsx')
    expect(page).toContain('DataMapCatalogPanel')
    expect(page).toContain('datamap-page-body')
    expect(page).toContain('ensureTable')
    expect(page).toContain('单击表查看字段')
    expect(page).toContain('搜索工作空间的表和字段')
    expect(page).toContain('业务说明')
    expect(page).toContain('getContext')
    expect(page).toContain('onNodeClick')
    expect(page).toContain('建表语句')
    expect(page).toContain('在数据探查中打开')
    expect(page).toContain('ensureTable')
    expect(page).not.toContain('高级收录')
    expect(page).not.toContain('收录到字典')
    expect(page).not.toContain('useSoftExpandedRows')
    expect(page).not.toMatch(/>注册</)

    const panel = read('components/DataMapCatalogPanel.tsx')
    expect(panel).toContain('fetchSchemas')
    expect(panel).toContain('fetchTables')
    expect(panel).toContain('fetchColumns')
    expect(panel).toContain('sqlSchemaCache')
    expect(panel).not.toContain('已收录')
    expect(panel).not.toContain('>目录<')
  })

  it('shares schema tree helpers and ensure-table fallback', () => {
    const tree = read('utils/sqlSchemaTree.ts')
    expect(tree).toContain('attachTables')
    expect(tree).toContain('attachColumns')

    const api = read('api/index.ts')
    expect(api).toContain("request.post('/datamap/ensure-table'")
    expect(api).toContain('status === 404')
  })
})

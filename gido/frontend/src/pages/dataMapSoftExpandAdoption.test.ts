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
    expect(page).toContain('打开右侧字典并自动收录')
    expect(page).not.toContain('useSoftExpandedRows')
    expect(page).not.toMatch(/>注册</)

    const panel = read('components/DataMapCatalogPanel.tsx')
    expect(panel).toContain('fetchSchemas')
    expect(panel).toContain('fetchTables')
    expect(panel).toContain('fetchColumns')
    expect(panel).toContain('sqlSchemaCache')
    expect(panel).toContain('已收录')
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

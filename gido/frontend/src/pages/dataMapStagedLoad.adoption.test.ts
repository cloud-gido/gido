/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图加载策略：左树走 sqlSchemaCache 懒加载；工具条不跟同步进度闪动。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DataMap staged load adoption', () => {
  it('uses catalog tree + cache refresh without toolbar sync chrome flicker', () => {
    const page = read('pages/DataMap.tsx')
    expect(page).toContain('DataMapCatalogPanel')
    expect(page).toContain('loadDatasources')
    expect(page).toContain('peekCachedDatasources')
    expect(page).toContain('catalogCapableDatasources')
    expect(page).toContain('invalidateSqlSchemaCache')
    expect(page).toContain('refreshToken')
    expect(page).toContain('ensureDetailExtra')
    expect(page).not.toContain('catalogSync')
    expect(page).not.toContain('同步物理表')
    expect(page).not.toContain('loading={dsLoading}')

    const panel = read('components/DataMapCatalogPanel.tsx')
    expect(panel).toContain('fetchSchemas')
    expect(panel).toContain('fetchTables')
    expect(panel).toContain('sqlSchemaCache')

    const dsPage = read('pages/Datasource.tsx')
    expect(dsPage).toContain('数据地图库白名单')
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DataMap staged load adoption', () => {
  it('loads registered tables first then progressive per-datasource catalog', () => {
    const page = read('pages/DataMap.tsx')
    expect(page).toContain('listLoading')
    expect(page).toContain('loadDatasources')
    expect(page).toContain('peekCachedDatasources')
    expect(page).toContain('catalogViewCache')
    expect(page).toContain('catalogCapableDatasources')
    expect(page).toContain('replaceDatasourceCatalogRows')
    expect(page).toContain('catalogSync')
    expect(page).toContain('ensureDetailExtra')
    expect(page).toContain('datasource_id: ds.id')
    expect(page).toMatch(/datamapApi\s*\n\s*\.catalog\(/)
  })
})

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
  it('loads quietly without toolbar sync chrome flicker', () => {
    const page = read('pages/DataMap.tsx')
    expect(page).toContain('listLoading')
    expect(page).toContain('loadDatasources')
    expect(page).toContain('peekCachedDatasources')
    expect(page).toContain('catalogViewCache')
    expect(page).toContain('catalogCapableDatasources')
    expect(page).toContain('replaceDatasourceCatalogRows')
    expect(page).toContain('hadCache')
    expect(page).toContain('userRefresh')
    expect(page).toContain('refreshing')
    expect(page).not.toContain('catalogSync')
    expect(page).not.toContain('同步物理表')
    expect(page).not.toContain('loading={dsLoading}')
    expect(page).toContain('ensureDetailExtra')
    expect(page).toContain('datasource_id: ds.id')
    expect(page).toMatch(/datamapApi\s*\n\s*\.catalog\(/)
  })
})

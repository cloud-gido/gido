/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fetchColumns,
  fetchSchemas,
  fetchTables,
  invalidateSqlSchemaCache,
} from './sqlSchemaCache'

vi.mock('../api', () => ({
  integrationApi: {
    listSchemas: vi.fn(async () => ({ schemas: [{ name: 'bigdata_ads', is_default: true }] })),
    listTables: vi.fn(async (_id: number, _kw: string, catalog?: string) => ({
      tables: [{ name: 'ads_foo', catalog: catalog || 'bigdata_ads', comment: '' }],
    })),
    getColumns: vi.fn(async () => ({
      columns: [{ name: 'stat_date', type: 'date' }],
    })),
  },
}))

import { integrationApi } from '../api'

describe('sqlSchemaCache', () => {
  beforeEach(() => {
    invalidateSqlSchemaCache()
    vi.clearAllMocks()
  })

  it('caches schemas and tables by datasource/catalog', async () => {
    await fetchSchemas(1)
    await fetchSchemas(1)
    expect(integrationApi.listSchemas).toHaveBeenCalledTimes(1)

    await fetchTables(1, 'bigdata_ads')
    await fetchTables(1, 'bigdata_ads')
    expect(integrationApi.listTables).toHaveBeenCalledTimes(1)

    await fetchTables(1, 'bigdata_dw')
    expect(integrationApi.listTables).toHaveBeenCalledTimes(2)
  })

  it('caches columns by catalog.table key', async () => {
    await fetchColumns(1, 'ads_foo', 'bigdata_ads')
    await fetchColumns(1, 'ads_foo', 'bigdata_ads')
    expect(integrationApi.getColumns).toHaveBeenCalledTimes(1)
    await fetchColumns(1, 'ads_bar', 'bigdata_ads')
    expect(integrationApi.getColumns).toHaveBeenCalledTimes(2)
  })
})

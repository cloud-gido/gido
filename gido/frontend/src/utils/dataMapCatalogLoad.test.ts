/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  catalogCapableDatasources,
  isCatalogCapableDatasource,
  mergeCatalogWithRegistered,
  replaceDatasourceCatalogRows,
  registeredToCatalogExtras,
} from './dataMapCatalogLoad'

describe('dataMapCatalogLoad', () => {
  it('filters catalog-capable datasources', () => {
    expect(isCatalogCapableDatasource({ ds_type: 'Doris' })).toBe(true)
    expect(isCatalogCapableDatasource({ ds_type: 'kafka' })).toBe(false)
    const list = catalogCapableDatasources(
      [
        { id: 1, ds_type: 'mysql' },
        { id: 2, ds_type: 'kafka' },
        { id: 3, ds_type: 'postgresql' },
      ],
      3,
    )
    expect(list.map(d => d.id)).toEqual([3])
  })

  it('shows registered extras until physical catalog covers them', () => {
    const registered = [
      { id: 10, datasource_id: 1, db_name: 'db', table_name: 'a', datasource_name: 'ds1' },
      { id: 11, datasource_id: 1, db_name: 'db', table_name: 'b', datasource_name: 'ds1' },
    ]
    const catalog = [
      {
        row_key: 'p-1-db-a',
        datasource_id: 1,
        catalog: 'db',
        table_name: 'a',
        registered: false,
      },
    ]
    const merged = mergeCatalogWithRegistered(catalog, registered)
    expect(merged.some(r => r.table_name === 'a' && r.row_key === 'p-1-db-a')).toBe(true)
    expect(merged.some(r => r.table_name === 'b' && r.meta_table_id === 11)).toBe(true)
    expect(registeredToCatalogExtras(registered)).toHaveLength(2)
  })

  it('replaces one datasource slice without dropping others', () => {
    const prev = [
      { datasource_id: 1, table_name: 'old' },
      { datasource_id: 2, table_name: 'keep' },
    ]
    const next = replaceDatasourceCatalogRows(prev, 1, [{ datasource_id: 1, table_name: 'new' }])
    expect(next.map(r => r.table_name).sort()).toEqual(['keep', 'new'])
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import {
  datasourceTagText,
  resolveDatasourceForRun,
} from './workspaceDatasource'

describe('resolveDatasourceForRun', () => {
  const ws = { default_datasource_id: 9 }

  it('keeps workspace source even before datasources list is loaded', () => {
    const info = resolveDatasourceForRun(undefined, ws, [])
    expect(info).toEqual({
      effectiveId: 9,
      effective: null,
      source: 'workspace',
      explicit: null,
    })
    expect(datasourceTagText(info)).toBe('空间默认')
  })

  it('fills name once list arrives without flipping to 未配置', () => {
    const before = datasourceTagText(resolveDatasourceForRun(undefined, ws, []))
    const after = datasourceTagText(resolveDatasourceForRun(
      undefined,
      ws,
      [{ id: 9, name: 'doris', ds_type: 'doris' }],
    ))
    expect(before).toBe('空间默认')
    expect(after).toBe('空间默认 doris (doris)')
    expect(before).not.toBe('未配置数据源')
  })

  it('says 未配置 only when neither node nor workspace has id', () => {
    const info = resolveDatasourceForRun(undefined, {}, [])
    expect(info.source).toBe('none')
    expect(datasourceTagText(info)).toBe('未配置数据源')
  })
})

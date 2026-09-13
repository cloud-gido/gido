/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

describe('Operation workflow filter adoption', () => {
  it('exposes searchable workflow Select like scheduler process filter', () => {
    const src = readFileSync(resolve(root, 'pages/Operation.tsx'), 'utf8')
    expect(src).toContain('workflowOptions')
    expect(src).toContain('workflowApi.listAll')
    expect(src).toContain('showSearch')
    expect(src).toContain('placeholder="工作流"')
    expect(src).toContain('optionFilterProp="label"')
  })
})

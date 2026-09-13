/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

describe('AlertCenter workflow filter adoption', () => {
  it('uses searchable workflow Select and surfaces workflow owners', () => {
    const src = readFileSync(resolve(root, 'pages/AlertCenter.tsx'), 'utf8')
    expect(src).toContain('workflowFilter')
    expect(src).toContain('workflowApi.listAll')
    expect(src).toContain('showSearch')
    expect(src).toContain('placeholder="工作流"')
    expect(src).toContain('workflow_id: workflowFilter')
    expect(src).toContain('workflow_created_by_username')
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it } from 'vitest'
import { leafTypeFromRow, resolveLeafTypeBadge } from './leafTypeBadge'

describe('leafTypeBadge', () => {
  it('maps stream / studio / probe fields', () => {
    expect(leafTypeFromRow({ job_type: 'JAR' })).toBe('JAR')
    expect(leafTypeFromRow({ node_type: 'PYTHON' })).toBe('PYTHON')
    expect(leafTypeFromRow({ leaf_type: 'SQL' })).toBe('SQL')
    expect(leafTypeFromRow({ job_type: 'SQL', node_type: 'PYTHON' })).toBe('SQL')
  })

  it('resolves short labels', () => {
    expect(resolveLeafTypeBadge('SQL').label).toBe('sql')
    expect(resolveLeafTypeBadge('JAR').label).toBe('jar')
    expect(resolveLeafTypeBadge('PYTHON').label).toBe('py')
    expect(resolveLeafTypeBadge('SHELL').label).toBe('sh')
  })
})

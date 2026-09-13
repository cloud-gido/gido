/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

describe('LineageGraph G6 lazy load', () => {
  it('does not statically import @antv/g6 (keeps DataMap first paint light)', () => {
    const src = readFileSync(resolve(root, 'components/LineageGraph.tsx'), 'utf8')
    expect(src).not.toMatch(/import G6 from ['"]@antv\/g6['"]/)
    expect(src).toMatch(/import\(['"]@antv\/g6['"]\)/)
  })
})

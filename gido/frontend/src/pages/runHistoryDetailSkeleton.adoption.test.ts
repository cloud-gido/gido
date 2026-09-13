/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

describe('RunHistoryDetail skeleton adoption', () => {
  it('shows page shell with Skeleton while detail loads', () => {
    const src = readFileSync(resolve(root, 'pages/RunHistoryDetail.tsx'), 'utf8')
    expect(src).toContain('Skeleton')
    expect(src).not.toContain('loading={loading}')
  })
})

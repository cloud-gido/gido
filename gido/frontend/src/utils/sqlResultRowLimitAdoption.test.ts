/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 回归：探查与数据开发共用 SQL 结果行上限 10000。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PROBE_DEFAULT_ROW_LIMIT, SQL_RESULT_ROW_CAP } from './sqlResultRowLimit'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('sql result row limit adoption', () => {
  it('Probe / Studio import shared SQL_RESULT_ROW_CAP', () => {
    const probe = read('pages/Probe.tsx')
    const studio = read('pages/Studio.tsx')
    expect(probe).toContain('sqlResultRowLimit')
    expect(probe).toContain('SQL_RESULT_ROW_CAP')
    expect(probe).toContain('PROBE_DEFAULT_ROW_LIMIT')
    expect(studio).toContain('SQL_RESULT_ROW_CAP')
    expect(SQL_RESULT_ROW_CAP).toBe(10000)
    expect(PROBE_DEFAULT_ROW_LIMIT).toBe(10000)
  })

  it('Probe export button no longer shows misleading current-row cap text', () => {
    const probe = read('pages/Probe.tsx')
    expect(probe).not.toContain('导出 CSV（最多 {activeStmt.rows.length} 行）')
    expect(probe).toContain('导出 CSV')
  })
})

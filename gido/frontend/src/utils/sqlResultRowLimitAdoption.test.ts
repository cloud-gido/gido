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
  it('Probe and the shared interactive dock import SQL_RESULT_ROW_CAP', () => {
    const probe = read('pages/Probe.tsx')
    const studio = read('pages/Studio.tsx')
    const dock = read('components/InteractiveRunDock.tsx')
    expect(probe).toContain('sqlResultRowLimit')
    expect(probe).toContain('SQL_RESULT_ROW_CAP')
    expect(probe).toContain('PROBE_DEFAULT_ROW_LIMIT')
    expect(studio).toContain('InteractiveRunDock')
    expect(dock).toContain('SQL_RESULT_ROW_CAP')
    expect(SQL_RESULT_ROW_CAP).toBe(10000)
    expect(PROBE_DEFAULT_ROW_LIMIT).toBe(10000)
  })

  it('Probe delegates snapshot export to the shared dock', () => {
    const probe = read('pages/Probe.tsx')
    const dock = read('components/InteractiveRunDock.tsx')
    const exportButton = read('components/InteractiveExportButton.tsx')
    expect(probe).not.toContain('导出 CSV（最多 {activeStmt.rows.length} 行）')
    expect(probe).not.toContain('exportRowsToCsv')
    expect(dock).toContain('InteractiveExportButton')
    expect(exportButton).toContain("csv: 'CSV'")
    expect(exportButton).toContain('onExport(format)')
    expect(dock).toContain('已物化快照')
  })
})

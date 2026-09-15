/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 数据地图：soft-expand + 打开即收录；稳态只读 getTable；注释自愈不挡首屏。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DataMap soft-expand adoption', () => {
  it('uses soft expand + resizable columns like instance center', () => {
    const src = read('pages/DataMap.tsx')
    expect(src).toContain('useSoftExpandedRows')
    expect(src).toContain('SoftRowDetailToggle')
    expect(src).toContain('gido-soft-expanded-panel')
    expect(src).toContain('useResizableTableColumns')
    expect(src).toContain('dw-resizable-table')
    expect(src).toContain('gido.datamap.catalog.cols')
  })

  it('opens/expands via ensureTable only when unregistered; no prominent 注册', () => {
    const src = read('pages/DataMap.tsx')
    expect(src).toContain('ensureTable')
    expect(src).toContain('ensureRowMeta')
    expect(src).toContain('已收录')
    expect(src).toContain('高级收录')
    expect(src).toContain('打开或展开未收录表即自动收录')
    // 稳态：已有 meta_table_id 早退，不盲目 ensure
    expect(src).toMatch(/if \(row\?\.meta_table_id\) return Number\(row\.meta_table_id\)/)
    expect(src).toContain('scheduleCommentHeal')
    expect(src).not.toMatch(/await maybeHealColumnComments/)
    // 主路径不再出现「注册」操作文案 / 确认框
    expect(src).not.toMatch(/>注册</)
    expect(src).not.toContain('注册到数据地图')
    expect(src).not.toContain('注册并打开')
  })

  it('api exposes ensure-table with tables fallback', () => {
    const api = read('api/index.ts')
    expect(api).toContain("request.post('/datamap/ensure-table'")
    expect(api).toContain("request.post('/datamap/tables'")
    expect(api).toContain('status === 404')
  })
})

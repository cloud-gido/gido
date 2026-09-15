/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Studio / Probe 共用 useSqlSchemaCompletion → monacoSqlCompletion 子句感知。
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('sql schema completion reuse', () => {
  it('shared hook binds clause-aware monaco provider', () => {
    const hook = read('hooks/useSqlSchemaCompletion.ts')
    expect(hook).toContain('bindMonacoSqlSchemaCompletion')
    const util = read('utils/monacoSqlCompletion.ts')
    expect(util).toContain('detectSqlSuggestSlot')
    expect(util).toContain('extractTableRefs')
    expect(util).toContain('currentSqlStatement')
    expect(util).toContain("slot === 'table_slot'")
    expect(util).toContain("slot === 'column_slot'")
  })

  it('Studio, Probe and Stream Studio adopt the shared hook', () => {
    for (const page of ['pages/Studio.tsx', 'pages/Probe.tsx', 'pages/StreamStudio.tsx']) {
      const src = read(page)
      expect(src).toContain('useSqlSchemaCompletion')
      expect(src).toContain('bindSqlSchemaCompletion')
    }
    const stream = read('pages/StreamStudio.tsx')
    expect(stream).toContain('resolveDatasourceForRun')
    expect(stream).toContain("selected?.job_type !== 'SQL'")
  })

  it('ranking helpers power professional sort / column detail', () => {
    const util = read('utils/monacoSqlCompletion.ts')
    expect(util).toContain('completionSortText')
    expect(util).toContain('formatColumnDetail')
    expect(util).toContain('pushFunctions')
    expect(util).toContain('markSqlCompletionRecent')
  })
})

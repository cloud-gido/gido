/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { describe, expect, it, vi } from 'vitest'
import { isRunnableSelection } from './monacoSelectionRunGlyph'
import { bindMonacoSelectionRunGlyph } from './monacoSelectionRunGlyph'

describe('isRunnableSelection', () => {
  it('识别完整 SELECT / WITH', () => {
    expect(isRunnableSelection('SELECT 1')).toBe(true)
    expect(
      isRunnableSelection(`SELECT id AS site_id
FROM bigdata_dw.dim_gameline_sys_operator
WHERE status = 1;`),
    ).toBe(true)
    expect(isRunnableSelection('WITH a AS (SELECT 1) SELECT * FROM a')).toBe(true)
  })

  it('跳过开头注释后再判断', () => {
    expect(
      isRunnableSelection(`-- 说明
SELECT 1`),
    ).toBe(true)
  })

  it('拒绝过短或非语句选区', () => {
    expect(isRunnableSelection('id')).toBe(false)
    expect(isRunnableSelection('status')).toBe(false)
    expect(isRunnableSelection('')).toBe(false)
    expect(isRunnableSelection('foo_bar_baz')).toBe(false)
  })
})

describe('bindMonacoSelectionRunGlyph', () => {
  it('选中可跑 SQL 时写入 glyph 装饰；清空选区时移除', () => {
    let selection: any = {
      isEmpty: () => false,
      startLineNumber: 51,
      endLineNumber: 54,
    }
    const decorations: any[] = []
    const ed = {
      updateOptions: vi.fn(),
      getSelection: () => selection,
      getModel: () => ({
        getValueInRange: () => 'SELECT 1 FROM t',
      }),
      getValue: () => '',
      deltaDecorations: (_old: string[], next: any[]) => {
        decorations.length = 0
        decorations.push(...next)
        return next.map((_, i) => `d${i}`)
      },
      onDidChangeCursorSelection: (fn: () => void) => {
        ;(ed as any)._sel = fn
        return { dispose: () => {} }
      },
      onDidChangeModelContent: () => ({ dispose: () => {} }),
      onMouseDown: () => ({ dispose: () => {} }),
    } as any

    const monaco = {
      Range: class {
        constructor(
          public startLineNumber: number,
          public startColumn: number,
          public endLineNumber: number,
          public endColumn: number,
        ) {}
      },
      editor: { MouseTargetType: { GUTTER_GLYPH_MARGIN: 2 } },
    }

    bindMonacoSelectionRunGlyph(ed, monaco, {
      onRun: vi.fn(),
    })
    expect(ed.updateOptions).toHaveBeenCalledWith({ glyphMargin: true })
    expect(decorations[0]?.options?.glyphMarginClassName).toBe('gido-sql-run-glyph')
    expect(decorations[0]?.range.startLineNumber).toBe(51)

    selection = { isEmpty: () => true, startLineNumber: 1, endLineNumber: 1 }
    ;(ed as any)._sel()
    expect(decorations).toHaveLength(0)
  })
})

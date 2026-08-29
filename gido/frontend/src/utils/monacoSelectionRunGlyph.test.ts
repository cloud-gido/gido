/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from 'vitest'
import { isRunnableSelection, bindMonacoSelectionRunGlyph } from './monacoSelectionRunGlyph'

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
  it('选中可跑 SQL 时显示 overlay 按钮；清空选区时隐藏', () => {
    let selection: any = {
      isEmpty: () => false,
      startLineNumber: 73,
      endLineNumber: 76,
    }
    const host = document.createElement('div')
    host.className = 'monaco-editor'
    document.body.appendChild(host)

    const ed = {
      updateOptions: vi.fn(),
      getDomNode: () => host,
      getSelection: () => selection,
      getModel: () => ({
        getValueInRange: () => 'SELECT DISTINCT company_id\nFROM t\nWHERE 1=1\nORDER BY 1;',
      }),
      getValue: () => '',
      getScrolledVisiblePosition: () => ({ top: 40, left: 0, height: 18 }),
      getLayoutInfo: () => ({
        decorationsLeft: 48,
        decorationsWidth: 18,
        contentLeft: 66,
      }),
      onDidChangeCursorSelection: (fn: () => void) => {
        ;(ed as any)._sel = fn
        return { dispose: () => {} }
      },
      onDidChangeModelContent: () => ({ dispose: () => {} }),
      onDidScrollChange: () => ({ dispose: () => {} }),
      onDidLayoutChange: () => ({ dispose: () => {} }),
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

    const disposables = bindMonacoSelectionRunGlyph(ed, monaco, { onRun: vi.fn() })
    expect(ed.updateOptions).toHaveBeenCalled()
    const btn = host.querySelector('.gido-sql-run-btn') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.hidden).toBe(false)
    expect(btn.style.top).toBeTruthy()

    selection = { isEmpty: () => true, startLineNumber: 1, endLineNumber: 1 }
    ;(ed as any)._sel()
    expect(btn.hidden).toBe(true)

    disposables.forEach(d => d.dispose())
    host.remove()
  })
})

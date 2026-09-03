/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 约束：API SQL 模板 / 审批预览须经 DwMonacoEditor，与 Studio 共用主题与外观。
 */
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DwMonacoEditor adoption', () => {
  it('ExpandableCodeArea and approval preview use DwMonacoEditor', () => {
    const expandable = read('components/ExpandableCodeArea.tsx')
    const approval = read('components/ApprovalResourcePreviewDrawer.tsx')
    expect(expandable).toMatch(/from ['"]\.\/DwMonacoEditor['"]/)
    expect(expandable).not.toMatch(/TextArea/)
    expect(approval).toMatch(/from ['"]\.\/DwMonacoEditor['"]/)
    expect(approval).not.toMatch(/@monaco-editor\/react/)
  })

  it('DwMonacoEditor wires shared appearance + theme registration', () => {
    const src = read('components/DwMonacoEditor.tsx')
    expect(src).toMatch(/registerDwMonacoThemes/)
    expect(src).toMatch(/monacoEditorOptionsFromAppearance/)
    expect(src).toMatch(/bindMonacoScriptKeybindings/)
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 约束：普通只读代码经 DwMonacoEditor；审批 CR 经共享 DiffViewer，并复用同一主题与外观。
 */
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '..')

function read(rel: string) {
  return readFileSync(resolve(root, rel), 'utf8')
}

describe('DwMonacoEditor adoption', () => {
  it('ExpandableCodeArea and approval preview use the shared Monaco wrappers', () => {
    const expandable = read('components/ExpandableCodeArea.tsx')
    const approval = read('components/ApprovalResourcePreviewDrawer.tsx')
    const approvalDiff = read('components/ApprovalDiffViewer.tsx')
    expect(expandable).toMatch(/from ['"]\.\/DwMonacoEditor['"]/)
    expect(expandable).not.toMatch(/TextArea/)
    expect(approval).toMatch(/from ['"]\.\/ApprovalDiffViewer['"]/)
    expect(approval).not.toMatch(/@monaco-editor\/react/)
    expect(approvalDiff).toMatch(/DiffEditor/)
    expect(approvalDiff).toMatch(/registerDwMonacoThemes/)
    expect(approvalDiff).toMatch(/monacoEditorOptionsFromAppearance/)
  })

  it('DwMonacoEditor wires shared appearance + theme registration', () => {
    const src = read('components/DwMonacoEditor.tsx')
    expect(src).toMatch(/registerDwMonacoThemes/)
    expect(src).toMatch(/monacoEditorOptionsFromAppearance/)
    expect(src).toMatch(/bindMonacoScriptKeybindings/)
  })
})

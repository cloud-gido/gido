/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ApprovalDiffViewer from './ApprovalDiffViewer'

const diffEditor = vi.fn(({ options }: any) => (
  <div data-testid="diff-layout">{String(options.renderSideBySide)}</div>
))

vi.mock('@monaco-editor/react', () => ({
  DiffEditor: (props: any) => diffEditor(props),
}))

describe('ApprovalDiffViewer', () => {
  it('defaults to unified diff and can switch side-by-side', () => {
    render(
      <ApprovalDiffViewer
        baselineLabel="生产版本 v1"
        artifact={{
          key: 'script',
          name: '脚本',
          kind: 'script',
          language: 'sql',
          baseline: 'SELECT 1',
          submitted: 'SELECT 2',
          changed: true,
          additions: 1,
          deletions: 1,
        }}
      />,
    )

    expect(screen.getByTestId('diff-layout')).toHaveTextContent('false')
    fireEvent.click(screen.getByText('并排视图'))
    expect(screen.getByTestId('diff-layout')).toHaveTextContent('true')
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import type { ReactNode } from 'react'
import { Tabs } from 'antd'
import { statementPresentation } from '../utils/statementPresentation'
import type { InteractiveRunStatement } from '../types/interactiveRun'

export default function StatementResultTabs({
  statements,
  activeKey,
  onChange,
  children,
}: {
  statements: InteractiveRunStatement[]
  activeKey: string
  onChange: (key: string) => void
  children: (statement: InteractiveRunStatement | null) => ReactNode
}) {
  const active = statements.find(item => String(item.index) === activeKey) ?? statements[0] ?? null

  return (
    <>
      {statements.length > 1 && (
        <Tabs
          size="small"
          activeKey={active ? String(active.index) : activeKey}
          onChange={onChange}
          style={{ padding: '0 8px', flexShrink: 0 }}
          items={statements.map(item => ({
            key: String(item.index),
            label: item.error || item.status === 'failed'
              ? `${statementPresentation(item).tabLabel} ${item.index + 1} ✕`
              : item.status === 'running'
                ? `${statementPresentation(item).tabLabel} ${item.index + 1} …`
                : `${statementPresentation(item).tabLabel} ${item.index + 1}`,
          }))}
        />
      )}
      {children(active)}
    </>
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState } from 'react'
import { DiffEditor } from '@monaco-editor/react'
import { Segmented, Space, Tag, Typography } from 'antd'
import {
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  registerDwMonacoThemes,
} from '../utils/editorAppearance'

const { Text } = Typography

export type ApprovalDiffArtifact = {
  key: string
  name: string
  kind: 'script' | 'json' | 'artifact'
  language: string
  baseline: string
  submitted: string
  changed: boolean
  additions: number
  deletions: number
}

type Props = {
  artifact: ApprovalDiffArtifact
  baselineLabel?: string
  height?: number
}

export default function ApprovalDiffViewer({
  artifact,
  baselineLabel = '对比基准',
  height = 420,
}: Props) {
  const [layout, setLayout] = useState<'unified' | 'side-by-side'>('unified')
  const appearance = loadEditorAppearance()

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
        <Space wrap>
          <Text strong>{artifact.name}</Text>
          {artifact.changed ? (
            <>
              <Tag color="green">+{artifact.additions}</Tag>
              <Tag color="red">-{artifact.deletions}</Tag>
            </>
          ) : <Tag>无变化</Tag>}
          <Text type="secondary">{baselineLabel} → 本次提交</Text>
        </Space>
        <Segmented
          size="small"
          value={layout}
          onChange={value => setLayout(value as 'unified' | 'side-by-side')}
          options={[
            { label: '统一视图', value: 'unified' },
            { label: '并排视图', value: 'side-by-side' },
          ]}
        />
      </Space>
      <div style={{ height, border: '1px solid #f0f0f0', borderRadius: 6, overflow: 'hidden' }}>
        <DiffEditor
          original={artifact.baseline || ''}
          modified={artifact.submitted || ''}
          language={artifact.language || 'plaintext'}
          theme={appearance.theme}
          beforeMount={registerDwMonacoThemes}
          options={{
            ...monacoEditorOptionsFromAppearance(appearance),
            readOnly: true,
            originalEditable: false,
            renderSideBySide: layout === 'side-by-side',
            renderIndicators: true,
            diffWordWrap: 'on',
            ignoreTrimWhitespace: false,
            renderOverviewRuler: true,
          }}
        />
      </div>
    </Space>
  )
}

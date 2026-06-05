/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState } from 'react'
import { Space, Select } from 'antd'
import {
  loadEditorAppearance,
  persistEditorAppearance,
  MONACO_THEME_OPTIONS,
  FONT_OPTIONS,
  type EditorAppearance,
} from '../utils/editorAppearance'

type Props = {
  /** 受控外观；不传则内部用 localStorage 初始化 */
  value?: EditorAppearance
  onChange?: (next: EditorAppearance) => void
}

const SIZE_OPTIONS = [12, 13, 14, 15, 16, 18].map(n => ({ value: n, label: `${n}px` }))

export default function EditorAppearanceToolbar({ value, onChange }: Props) {
  const [inner, setInner] = useState<EditorAppearance>(() => value ?? loadEditorAppearance())
  const a = value ?? inner

  const commit = (patch: Partial<EditorAppearance>) => {
    const merged = { ...a, ...patch }
    const next = persistEditorAppearance(merged)
    if (value === undefined) setInner(next)
    onChange?.(next)
  }

  return (
    <Space size={6} wrap align="center">
      <span style={{ color: '#888', fontSize: 12 }}>主题</span>
      <Select
        size="small"
        style={{ minWidth: 148 }}
        popupMatchSelectWidth={false}
        options={MONACO_THEME_OPTIONS}
        value={a.theme}
        onChange={v => commit({ theme: v })}
      />
      <span style={{ color: '#888', fontSize: 12 }}>字体</span>
      <Select
        size="small"
        style={{ minWidth: 140 }}
        options={FONT_OPTIONS}
        value={a.fontId}
        onChange={v => commit({ fontId: v })}
      />
      <Select
        size="small"
        style={{ width: 76 }}
        options={SIZE_OPTIONS}
        value={a.fontSize}
        onChange={v => commit({ fontSize: v })}
      />
    </Space>
  )
}

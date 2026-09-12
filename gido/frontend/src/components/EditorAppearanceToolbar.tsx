/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 *
 * 编辑器主题/字体/字号。默认收纳进「更多」面板，避免工作台工具栏在小屏挤爆。
 */
import { useState, type ReactNode } from 'react'
import { Button, Divider, Dropdown, Select, Space } from 'antd'
import { MoreOutlined } from '@ant-design/icons'
import {
  loadEditorAppearance,
  persistEditorAppearance,
  MONACO_THEME_OPTIONS,
  FONT_OPTIONS,
  type EditorAppearance,
} from '../utils/editorAppearance'
import './EditorAppearanceToolbar.css'

type Props = {
  /** 受控外观；不传则内部用 localStorage 初始化 */
  value?: EditorAppearance
  onChange?: (next: EditorAppearance) => void
  /**
   * menu（默认）：收纳到「更多」按钮，适合 Studio / Probe / Stream 工具栏
   * inline：原三下拉横排（兼容旧用法）
   */
  variant?: 'menu' | 'inline'
  /** 「更多」面板里额外低频入口（版本历史等） */
  extra?: ReactNode
  menuLabel?: string
}

const SIZE_OPTIONS = [12, 13, 14, 15, 16, 18].map(n => ({ value: n, label: `${n}px` }))

function AppearanceFields({
  a,
  commit,
}: {
  a: EditorAppearance
  commit: (patch: Partial<EditorAppearance>) => void
}) {
  const popupContainer = (node: HTMLElement) =>
    (node.closest('.gido-editor-more-panel') as HTMLElement) || document.body

  return (
    <div className="gido-editor-appearance-fields">
      <label className="gido-editor-appearance-fields__row">
        <span>主题</span>
        <Select
          size="small"
          style={{ flex: 1, minWidth: 140 }}
          popupMatchSelectWidth={false}
          getPopupContainer={popupContainer}
          options={MONACO_THEME_OPTIONS}
          value={a.theme}
          onChange={v => commit({ theme: v })}
        />
      </label>
      <label className="gido-editor-appearance-fields__row">
        <span>字体</span>
        <Select
          size="small"
          style={{ flex: 1, minWidth: 140 }}
          getPopupContainer={popupContainer}
          options={FONT_OPTIONS}
          value={a.fontId}
          onChange={v => commit({ fontId: v })}
        />
      </label>
      <label className="gido-editor-appearance-fields__row">
        <span>字号</span>
        <Select
          size="small"
          style={{ width: 88 }}
          getPopupContainer={popupContainer}
          options={SIZE_OPTIONS}
          value={a.fontSize}
          onChange={v => commit({ fontSize: v })}
        />
      </label>
    </div>
  )
}

export default function EditorAppearanceToolbar({
  value,
  onChange,
  variant = 'menu',
  extra,
  menuLabel = '更多',
}: Props) {
  const [inner, setInner] = useState<EditorAppearance>(() => value ?? loadEditorAppearance())
  const a = value ?? inner

  const commit = (patch: Partial<EditorAppearance>) => {
    const merged = { ...a, ...patch }
    const next = persistEditorAppearance(merged)
    if (value === undefined) setInner(next)
    onChange?.(next)
  }

  if (variant === 'inline') {
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
        {extra}
      </Space>
    )
  }

  return (
    <Dropdown
      trigger={['click']}
      placement="bottomRight"
      dropdownRender={() => (
        <div className="gido-editor-more-panel" onClick={e => e.stopPropagation()}>
          <div className="gido-editor-more-panel__title">编辑器外观</div>
          <AppearanceFields a={a} commit={commit} />
          {extra ? (
            <>
              <Divider style={{ margin: '10px 0' }} />
              <div className="gido-editor-more-panel__extra">{extra}</div>
            </>
          ) : null}
        </div>
      )}
    >
      <Button size="small" type="text" icon={<MoreOutlined />}>
        {menuLabel}
      </Button>
    </Dropdown>
  )
}

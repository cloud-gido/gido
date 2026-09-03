/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 可全屏的 SQL/代码编辑（Form.Item 兼容 value/onChange）。
 * 编辑器视觉与快捷键走共享 DwMonacoEditor；全屏交互对齐工作流 DAG（Portal + Esc）。
 */
import { useEffect, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Button, Space, Typography } from 'antd'
import { FullscreenExitOutlined, FullscreenOutlined } from '@ant-design/icons'
import { Z_FULLSCREEN } from './dagEditorOverlay'
import DwMonacoEditor from './DwMonacoEditor'

const { Text } = Typography

type Props = {
  value?: string
  onChange?: (value: string) => void
  rows?: number
  /** 保留以兼容调用方；Monaco 无原生 placeholder，由 title 提示 */
  placeholder?: string
  /** 全屏顶栏标题 */
  title?: string
  disabled?: boolean
  style?: CSSProperties
  language?: string
}

export default function ExpandableCodeArea({
  value,
  onChange,
  rows = 6,
  placeholder: _placeholder,
  title = 'SQL 模板',
  disabled,
  style,
  language = 'sql',
}: Props) {
  const [fullscreen, setFullscreen] = useState(false)
  const inlineHeight = Math.max(160, rows * 22)

  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        setFullscreen(false)
      }
    }
    window.addEventListener('keydown', onKey, true)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = prev
    }
  }, [fullscreen])

  const toolbar = (inFs: boolean) => (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 8,
      marginBottom: 8,
      flexShrink: 0,
    }}
    >
      <Space size={8}>
        <Text strong={inFs}>{title}</Text>
        {inFs ? <Text type="secondary" style={{ fontSize: 12 }}>Esc 退出全屏</Text> : null}
      </Space>
      <Button
        type="text"
        size="small"
        icon={inFs ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
        disabled={disabled}
        onClick={() => setFullscreen(v => !v)}
        aria-label={inFs ? '退出全屏' : '全屏'}
      >
        {inFs ? '退出全屏' : '全屏'}
      </Button>
    </div>
  )

  const editor = (height: string | number, fill?: boolean) => (
    <DwMonacoEditor
      value={value}
      onChange={onChange}
      height={height}
      language={language}
      readOnly={Boolean(disabled)}
      style={fill ? { height: '100%', ...style } : style}
    />
  )

  const inline = (
    <div data-testid="expandable-code-area">
      {toolbar(false)}
      {editor(inlineHeight)}
    </div>
  )

  if (!fullscreen) return inline

  const shell = (
    <div
      data-testid="expandable-code-area-fullscreen"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: Z_FULLSCREEN,
        background: '#fff',
        display: 'flex',
        flexDirection: 'column',
        padding: 16,
        boxSizing: 'border-box',
      }}
    >
      {toolbar(true)}
      <div style={{ flex: 1, minHeight: 0 }}>
        {editor('100%', true)}
      </div>
    </div>
  )

  return (
    <>
      {/* 占位，避免 Modal 表单高度塌缩 */}
      <div style={{ minHeight: inlineHeight + 48 }} aria-hidden>
        {toolbar(false)}
        <div style={{ height: inlineHeight, visibility: 'hidden' }} />
      </div>
      {createPortal(shell, document.body)}
    </>
  )
}

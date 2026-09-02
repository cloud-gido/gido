/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 可全屏的代码/SQL 文本域（Form.Item 兼容 value/onChange）。
 * 交互对齐工作流 DAG：Portal 盖住 Modal、Esc 退出。
 */
import { useEffect, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Button, Input, Space, Typography } from 'antd'
import { FullscreenExitOutlined, FullscreenOutlined } from '@ant-design/icons'
import { Z_FULLSCREEN } from './dagEditorOverlay'

const { TextArea } = Input
const { Text } = Typography

type Props = {
  value?: string
  onChange?: (value: string) => void
  rows?: number
  placeholder?: string
  /** 全屏顶栏标题 */
  title?: string
  disabled?: boolean
  style?: CSSProperties
}

export default function ExpandableCodeArea({
  value,
  onChange,
  rows = 6,
  placeholder,
  title = 'SQL 模板',
  disabled,
  style,
}: Props) {
  const [fullscreen, setFullscreen] = useState(false)

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

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    onChange?.(e.target.value)
  }

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

  const areaStyle: CSSProperties = {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 13,
    lineHeight: 1.5,
    ...style,
  }

  const inline = (
    <div data-testid="expandable-code-area">
      {toolbar(false)}
      <TextArea
        value={value}
        onChange={handleChange}
        rows={rows}
        placeholder={placeholder}
        disabled={disabled}
        style={areaStyle}
      />
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
      <TextArea
        value={value}
        onChange={handleChange}
        placeholder={placeholder}
        disabled={disabled}
        style={{
          ...areaStyle,
          flex: 1,
          height: '100%',
          resize: 'none',
        }}
        autoFocus
      />
    </div>
  )

  return (
    <>
      {/* 占位，避免 Modal 表单高度塌缩 */}
      <div style={{ minHeight: rows * 22 + 48 }} aria-hidden>
        {toolbar(false)}
        <TextArea rows={rows} value={value} disabled style={{ ...areaStyle, visibility: 'hidden' }} />
      </div>
      {createPortal(shell, document.body)}
    </>
  )
}

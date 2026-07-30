/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 编辑器下方结果/日志坞：统一标题栏、页签与右上角关闭，供 Studio / 探查 / Stream 复用。
 */
import type { ReactNode } from 'react'
import { Button } from 'antd'
import { CloseCircleOutlined } from '@ant-design/icons'

export type EditorResultDockTab = {
  key: string
  label: ReactNode
  children: ReactNode
}

type Props = {
  tabs: EditorResultDockTab[]
  activeKey: string
  onChange?: (key: string) => void
  onClose: () => void
  /** 关闭按钮左侧额外操作（如 Stream 预览行数） */
  extra?: ReactNode
  closeTitle?: string
}

export default function EditorResultDock({
  tabs,
  activeKey,
  onChange,
  onClose,
  extra,
  closeTitle = '关闭结果面板',
}: Props) {
  const active = tabs.find(t => t.key === activeKey) ?? tabs[0]
  const single = tabs.length <= 1

  return (
    <div
      style={{
        background: '#fafafa',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '0 12px',
          background: '#fff',
          borderBottom: '1px solid #f0f0f0',
          display: 'flex',
          alignItems: 'center',
          gap: 0,
          minHeight: 40,
          flexShrink: 0,
        }}
      >
        {tabs.map(t => {
          const selected = (active?.key ?? activeKey) === t.key
          return (
            <div
              key={t.key}
              role={single ? undefined : 'tab'}
              aria-selected={single ? undefined : selected}
              onClick={() => {
                if (!single) onChange?.(t.key)
              }}
              style={{
                padding: '0 14px',
                height: 40,
                lineHeight: '40px',
                cursor: single ? 'default' : 'pointer',
                fontSize: 13,
                color: selected ? '#1677ff' : '#666',
                fontWeight: selected ? 600 : 400,
                borderBottom: selected ? '2px solid #1677ff' : '2px solid transparent',
                userSelect: 'none',
              }}
            >
              {t.label}
            </div>
          )
        })}
        <div style={{ flex: 1 }} />
        {extra}
        <Button
          type="text"
          size="small"
          icon={<CloseCircleOutlined />}
          style={{ color: '#999' }}
          title={closeTitle}
          aria-label={closeTitle}
          onClick={onClose}
        />
      </div>
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          background: '#fff',
        }}
      >
        {active?.children}
      </div>
    </div>
  )
}

/** 结果页签上的绿色行数角标（与 Studio / 探查一致） */
export function EditorResultRowBadge({ count }: { count: number }) {
  return (
    <span style={{ marginLeft: 8, color: '#52c41a', fontSize: 12, fontWeight: 400 }}>
      {count} 行
    </span>
  )
}

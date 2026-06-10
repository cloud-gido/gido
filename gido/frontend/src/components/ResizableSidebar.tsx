/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useRef, useState, type CSSProperties, type ReactNode } from 'react'

type Props = {
  defaultWidth: number
  minWidth?: number
  maxWidth?: number
  storageKey: string
  style?: CSSProperties
  left: ReactNode
  right: ReactNode
}

/** 左侧定宽 + 拖拽调整 + 右侧自适应 */
export default function ResizableSidebar({
  defaultWidth,
  minWidth = 160,
  maxWidth = 720,
  storageKey,
  style,
  left,
  right,
}: Props) {
  const readStored = () => {
    try {
      const v = Number(localStorage.getItem(storageKey))
      if (Number.isFinite(v) && v >= minWidth && v <= maxWidth) return v
    } catch {
      /* ignore */
    }
    return defaultWidth
  }

  const [width, setWidth] = useState(readStored)
  const widthRef = useRef(width)
  widthRef.current = width

  const dragRef = useRef<{ startX: number; startW: number } | null>(null)

  const onMove = useCallback(
    (e: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      const dx = e.clientX - d.startX
      const next = Math.min(maxWidth, Math.max(minWidth, d.startW + dx))
      widthRef.current = next
      setWidth(next)
    },
    [minWidth, maxWidth],
  )

  const onUp = useCallback(() => {
    if (!dragRef.current) return
    dragRef.current = null
    try {
      localStorage.setItem(storageKey, String(widthRef.current))
    } catch {
      /* ignore */
    }
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }, [onMove, storageKey])

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startW: widthRef.current }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div style={{ display: 'flex', ...style }}>
      <div style={{ width, flexShrink: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>{left}</div>
      <div
        role="separator"
        aria-orientation="vertical"
        onMouseDown={onMouseDown}
        style={{
          width: 6,
          flexShrink: 0,
          cursor: 'col-resize',
          background: 'linear-gradient(90deg, #f0f0f0 0, #e8e8e8 50%, #f0f0f0 100%)',
          borderLeft: '1px solid #e0e0e0',
          borderRight: '1px solid #e0e0e0',
        }}
      />
      <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>{right}</div>
    </div>
  )
}

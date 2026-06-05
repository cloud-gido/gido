/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useRef, useState, type CSSProperties, type ReactNode } from 'react'

type Props = {
  top: ReactNode
  bottom: ReactNode
  /** 上方区域 flex-grow 占比（与下方之和为 1），如 0.58 表示约 58% 高度给上方 */
  defaultTopRatio?: number
  minTopRatio?: number
  minBottomRatio?: number
  storageKey?: string
  style?: CSSProperties
}

/** 上下分栏 + 拖拽调整高度比例，可选写入 localStorage */
export default function ResizableVerticalSplit({
  top,
  bottom,
  defaultTopRatio = 0.58,
  minTopRatio = 0.22,
  minBottomRatio = 0.18,
  storageKey,
  style,
}: Props) {
  const maxTopRatio = 1 - minBottomRatio

  const readStored = () => {
    if (!storageKey) return defaultTopRatio
    try {
      const v = Number(localStorage.getItem(storageKey))
      if (Number.isFinite(v) && v >= minTopRatio && v <= maxTopRatio) return v
    } catch {
      /* ignore */
    }
    return defaultTopRatio
  }

  const [topRatio, setTopRatio] = useState(readStored)
  const topRatioRef = useRef(topRatio)
  topRatioRef.current = topRatio
  const containerRef = useRef<HTMLDivElement>(null)

  const dragRef = useRef<{ startY: number; startRatio: number; height: number } | null>(null)

  const onMove = useCallback(
    (e: MouseEvent) => {
      const d = dragRef.current
      if (!d) return
      const deltaY = e.clientY - d.startY
      const frac = deltaY / Math.max(d.height, 1)
      // 向下拖分隔条 → 分隔条下移 → 上方区域变大（与 macOS 常见分割条一致）
      const next = Math.min(maxTopRatio, Math.max(minTopRatio, d.startRatio + frac))
      topRatioRef.current = next
      setTopRatio(next)
    },
    [minTopRatio, maxTopRatio],
  )

  const onUp = useCallback(() => {
    if (!dragRef.current) return
    dragRef.current = null
    if (storageKey) {
      try {
        localStorage.setItem(storageKey, String(topRatioRef.current))
      } catch {
        /* ignore */
      }
    }
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }, [onMove, storageKey])

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault()
    const h = containerRef.current?.getBoundingClientRect().height ?? 400
    dragRef.current = { startY: e.clientY, startRatio: topRatioRef.current, height: h }
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return (
    <div
      ref={containerRef}
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        ...style,
      }}
    >
      <div style={{ flex: `${topRatio} 1 0px`, minHeight: 80, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {top}
      </div>
      <div
        role="separator"
        aria-orientation="horizontal"
        onMouseDown={onMouseDown}
        style={{
          height: 6,
          flexShrink: 0,
          cursor: 'row-resize',
          background: 'linear-gradient(180deg, #f5f5f5 0, #e8e8e8 50%, #f5f5f5 100%)',
          borderTop: '1px solid #e0e0e0',
          borderBottom: '1px solid #e0e0e0',
        }}
      />
      <div
        style={{
          flex: `${1 - topRatio} 1 0px`,
          minHeight: 120,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {bottom}
      </div>
    </div>
  )
}

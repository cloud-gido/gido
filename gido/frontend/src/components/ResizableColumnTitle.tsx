/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useRef } from 'react'
import './queryResultPanel.css'

type Props = {
  title: string
  width: number
  minWidth?: number
  maxWidth?: number
  onResize: (width: number) => void
}

/** Ant Design Table 表头：单行标题 + 右侧拖动手柄（与查询结果表列宽交互一致） */
export default function ResizableColumnTitle({
  title,
  width,
  minWidth = 64,
  maxWidth = 480,
  onResize,
}: Props) {
  const drag = useRef<{ startX: number; startW: number } | null>(null)

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      drag.current = { startX: e.clientX, startW: width }
      const onMove = (ev: MouseEvent) => {
        const d = drag.current
        if (!d) return
        const nw = Math.max(minWidth, Math.min(maxWidth, d.startW + (ev.clientX - d.startX)))
        onResize(nw)
      }
      const onUp = () => {
        drag.current = null
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [width, minWidth, maxWidth, onResize],
  )

  return (
    <div
      className="dw-col-header-root"
      style={{ display: 'flex', alignItems: 'center', width: '100%', whiteSpace: 'nowrap' }}
    >
      <span
        className="dw-col-header-title"
        style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}
        title={title}
      >
        {title}
      </span>
      <div
        role="separator"
        aria-orientation="vertical"
        className="dw-col-resize-handle"
        onMouseDown={onMouseDown}
        title="拖拽调整列宽"
      />
    </div>
  )
}

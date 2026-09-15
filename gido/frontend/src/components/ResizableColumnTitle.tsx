/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useRef } from 'react'
import { markColumnResizeGesture } from '../utils/columnResizeGesture'
import './queryResultPanel.css'

type Props = {
  title: string
  width: number
  minWidth?: number
  maxWidth?: number
  onResize: (width: number) => void
}

/**
 * Ant Design Table 表头：标题 + 右侧拖动手柄。
 * - 拖拽与排序互不打扰（stopPropagation + 松手后短暂抑制 sorter）
 * - 拖动中 rAF 合并回调，避免大表每像素全量重渲染
 */
export default function ResizableColumnTitle({
  title,
  width,
  minWidth = 64,
  maxWidth = 520,
  onResize,
}: Props) {
  const drag = useRef<{ startX: number; startW: number; moved: boolean } | null>(null)
  const raf = useRef(0)
  const pending = useRef<number | null>(null)

  const flush = useCallback(() => {
    raf.current = 0
    if (pending.current == null) return
    onResize(pending.current)
  }, [onResize])

  const stopAll = useCallback((e: React.SyntheticEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      stopAll(e)
      markColumnResizeGesture(60_000) // 拖拽全程抑制，直到 mouseup 再缩短窗口
      drag.current = { startX: e.clientX, startW: width, moved: false }

      const onMove = (ev: MouseEvent) => {
        const d = drag.current
        if (!d) return
        if (Math.abs(ev.clientX - d.startX) > 2) d.moved = true
        const nw = Math.max(minWidth, Math.min(maxWidth, d.startW + (ev.clientX - d.startX)))
        pending.current = nw
        if (!raf.current) raf.current = window.requestAnimationFrame(flush)
      }
      const onUp = () => {
        const d = drag.current
        drag.current = null
        if (raf.current) {
          window.cancelAnimationFrame(raf.current)
          raf.current = 0
        }
        if (pending.current != null) {
          onResize(pending.current)
          pending.current = null
        }
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        window.removeEventListener('mousemove', onMove)
        window.removeEventListener('mouseup', onUp)
        // 松手后仍抑制一次 click，避免 th 收到合成 click 触发排序
        markColumnResizeGesture(d?.moved ? 450 : 200)
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('mousemove', onMove)
      window.addEventListener('mouseup', onUp)
    },
    [width, minWidth, maxWidth, onResize, flush, stopAll],
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
        onClick={stopAll}
        onDoubleClick={stopAll}
        title="拖拽调整列宽"
      />
    </div>
  )
}

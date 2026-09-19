/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 *
 * G6 约 1MB+：按需动态 import，避免数据字典列表首屏拖慢。
 */
import { useEffect, useRef, useState } from 'react'
import { Spin } from 'antd'

interface LineageGraphProps {
  data: { nodes: any[], edges: any[] }
  currentTableId?: number
  height?: number
  onNodeClick?: (tableId: number) => void
}

export default function LineageGraph({ data, currentTableId, height = 400, onNodeClick }: LineageGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)
  const onNodeClickRef = useRef(onNodeClick)
  onNodeClickRef.current = onNodeClick
  const [engineLoading, setEngineLoading] = useState(false)

  useEffect(() => {
    if (!containerRef.current || !data.nodes.length) return
    let cancelled = false
    let resizeObserver: ResizeObserver | null = null

    ;(async () => {
      setEngineLoading(true)
      const G6 = (await import('@antv/g6')).default
      if (cancelled || !containerRef.current) return
      setEngineLoading(false)

      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }

      const graph = new G6.Graph({
        container: containerRef.current,
        width: containerRef.current.offsetWidth || 800,
        height,
        fitView: true,
        fitViewPadding: 40,
        layout: {
          type: 'dagre',
          rankdir: 'LR',
          nodesep: 40,
          ranksep: 80,
        },
        defaultNode: {
          type: 'rect',
          size: [160, 48],
          style: { fill: '#fff', stroke: '#1677ff', lineWidth: 2, radius: 6, cursor: 'pointer' },
          labelCfg: { style: { fill: '#333', fontSize: 13 } },
        },
        defaultEdge: {
          type: 'polyline',
          style: {
            stroke: '#1677ff',
            lineWidth: 2,
            endArrow: { path: G6.Arrow.triangle(8, 8, 0), fill: '#1677ff' },
          },
        },
        modes: {
          default: ['drag-canvas', 'zoom-canvas', 'drag-node'],
        },
      })

      const graphData = {
        nodes: data.nodes.map(n => ({
          id: String(n.id),
          label: n.name,
          style: n.id === currentTableId
            ? { fill: '#e6f4ff', stroke: '#1677ff', lineWidth: 3 }
            : { fill: '#fff', stroke: '#d9d9d9' },
        })),
        edges: data.edges.map((e, idx) => ({
          id: `edge_${idx}`,
          source: String(e.source),
          target: String(e.target),
          label: e.task_name || '',
          labelCfg: { style: { fill: '#8c8c8c', fontSize: 11 } },
        })),
      }

      graph.data(graphData)
      graph.render()
      graph.on('node:click', (evt: any) => {
        const id = Number(evt?.item?.getModel?.()?.id)
        if (Number.isFinite(id)) onNodeClickRef.current?.(id)
      })
      graphRef.current = graph

      resizeObserver = new ResizeObserver(() => {
        if (containerRef.current && graphRef.current) {
          graphRef.current.changeSize(containerRef.current.offsetWidth, height)
          graphRef.current.fitView()
        }
      })
      resizeObserver.observe(containerRef.current)
    })().catch(() => {
      if (!cancelled) setEngineLoading(false)
    })

    return () => {
      cancelled = true
      resizeObserver?.disconnect()
      if (graphRef.current) {
        graphRef.current.destroy()
        graphRef.current = null
      }
    }
  }, [data, currentTableId, height])

  if (!data.edges?.length) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', border: '1px dashed #d9d9d9', borderRadius: 4 }}>
        暂无血缘。保存引用这张表的 SQL、同步任务或实时作业后，这里会显示上下游。
      </div>
    )
  }

  return (
    <div style={{ position: 'relative', border: '1px solid #f0f0f0', borderRadius: 4 }}>
      {engineLoading && (
        <div style={{ position: 'absolute', inset: 0, zIndex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.72)' }}>
          <Spin tip="加载图谱引擎…" />
        </div>
      )}
      <div ref={containerRef} style={{ minHeight: height }} />
      {onNodeClick && (
        <div style={{ padding: '6px 10px', fontSize: 12, color: '#8c8c8c' }}>单击节点打开对应表</div>
      )}
    </div>
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useRef } from 'react'
import G6 from '@antv/g6'

interface LineageGraphProps {
  data: { nodes: any[], edges: any[] }
  currentTableId?: number
  height?: number
}

export default function LineageGraph({ data, currentTableId, height = 400 }: LineageGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<any>(null)

  useEffect(() => {
    if (!containerRef.current || !data.nodes.length) return

    if (graphRef.current) {
      graphRef.current.destroy()
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
        ranksep: 80
      },
      defaultNode: {
        type: 'rect',
        size: [160, 48],
        style: { fill: '#fff', stroke: '#1677ff', lineWidth: 2, radius: 6 },
        labelCfg: { style: { fill: '#333', fontSize: 13 } }
      },
      defaultEdge: {
        type: 'polyline',
        style: { stroke: '#1677ff', lineWidth: 2, endArrow: { path: G6.Arrow.triangle(8, 8, 0), fill: '#1677ff' } }
      },
      modes: {
        default: ['drag-canvas', 'zoom-canvas', 'drag-node']
      }
    })

    const graphData = {
      nodes: data.nodes.map(n => ({
        id: String(n.id),
        label: n.name,
        style: n.id === currentTableId
          ? { fill: '#e6f4ff', stroke: '#1677ff', lineWidth: 3 }
          : { fill: '#fff', stroke: '#d9d9d9' }
      })),
      edges: data.edges.map((e, idx) => ({
        id: `edge_${idx}`,
        source: String(e.source),
        target: String(e.target)
      }))
    }

    graph.data(graphData)
    graph.render()
    graphRef.current = graph

    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        graph.changeSize(containerRef.current.offsetWidth, height)
        graph.fitView()
      }
    })
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      graph.destroy()
    }
  }, [data, currentTableId, height])

  if (!data.nodes.length) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#bbb', border: '1px dashed #d9d9d9', borderRadius: 4 }}>
        暂无血缘数据
      </div>
    )
  }

  return <div ref={containerRef} style={{ border: '1px solid #f0f0f0', borderRadius: 4 }} />
}

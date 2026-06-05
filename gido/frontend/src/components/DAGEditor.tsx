/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useRef, useCallback, useState, forwardRef, useImperativeHandle } from 'react'
import { Select, Button, Tag, Tooltip } from 'antd'
import { DeleteOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { Graph, Shape } from '@antv/x6'

interface DAGEditorProps {
  nodes: any[]
  value?: { nodes: any[], edges: any[] }
  onChange?: (dag: { nodes: any[], edges: any[] }) => void
  onNodeDoubleClick?: (nodeId: number) => void
}

export interface DAGEditorRef {
  getDAG: () => { nodes: any[], edges: any[] }
}

const TYPE_COLOR: Record<string, string> = {
  SQL: '#1677ff', PYTHON: '#52c41a', SHELL: '#fa8c16', VIRTUAL: '#999', SYNC: '#722ed1'
}

const DAGEditor = forwardRef<DAGEditorRef, DAGEditorProps>(function DAGEditor({ nodes, value, onChange, onNodeDoubleClick }, ref) {
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const [selectedCell, setSelectedCell] = useState<string | null>(null)
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange

  useImperativeHandle(ref, () => ({ getDAG: () => _readDAG() }))

  const nodeMap = Object.fromEntries(nodes.map(n => [n.id, n]))

  const scheduleSync = useCallback(() => {
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current)
    syncTimerRef.current = setTimeout(() => {
      onChangeRef.current?.(_readDAG())
    }, 200)
  }, [])

  const _readDAG = () => {
    const graph = graphRef.current
    if (!graph) return { nodes: [], edges: [] }
    return {
      nodes: graph.getNodes().map(n => ({
        node_id: Number(n.id),
        x: Math.round(n.getPosition().x),
        y: Math.round(n.getPosition().y),
      })),
      edges: graph.getEdges().map(e => ({
        source: Number(e.getSourceCellId()),
        target: Number(e.getTargetCellId()),
      })),
    }
  }

  useEffect(() => {
    if (!containerRef.current) return

    const graph = new Graph({
      container: containerRef.current,
      width: containerRef.current.offsetWidth || 680,
      height: 340,
      background: { color: '#fafafa' },
      grid: { visible: true, size: 16 } as any,
      interacting: {
        nodeMovable: true,
        edgeMovable: false,
        magnetConnectable: true,
      },
      connecting: {
        snap: true,
        allowBlank: false,
        allowLoop: false,
        allowMulti: false,
        highlight: true,
        connector: 'rounded',
        connectionPoint: 'anchor',
        router: { name: 'er', args: { direction: 'H' } },
        createEdge() {
          return new Shape.Edge({
            attrs: {
              line: { stroke: '#1677ff', strokeWidth: 1.5, targetMarker: { name: 'block', size: 8 } },
            },
          })
        },
        validateConnection({ targetMagnet }: any) {
          return !!targetMagnet
        },
      },
      mousewheel: { enabled: true, zoomAtMousePosition: true, modifiers: 'ctrl', minScale: 0.5, maxScale: 2 },
      panning: { enabled: true, modifiers: 'shift' },
    } as any)

    graph.on('node:click', ({ node }: any) => setSelectedCell(node.id))
    graph.on('node:dblclick', ({ node }: any) => onNodeDoubleClick?.(Number(node.id)))
    graph.on('edge:click', ({ edge }: any) => setSelectedCell(edge.id))
    graph.on('blank:click', () => setSelectedCell(null))
    graph.on('node:change:position', scheduleSync)
    graph.on('edge:connected', scheduleSync)
    graph.on('edge:removed', scheduleSync)
    graph.on('node:removed', scheduleSync)

    graphRef.current = graph

    if (value?.nodes?.length) loadDAG(graph, value, nodeMap)

    return () => {
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current)
      graph.dispose()
    }
  }, [])

  const nodeIdKey = JSON.stringify((value?.nodes || []).map((n: any) => n.node_id).sort())
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    graph.clearCells()
    if (value?.nodes?.length) loadDAG(graph, value, nodeMap)
  }, [nodeIdKey, nodes.length])

  const loadDAG = (graph: Graph, dag: any, nm: any) => {
    const nodeIds = new Set(dag.nodes.map((n: any) => n.node_id))
    dag.nodes.forEach((n: any, i: number) => {
      const info = nm[n.node_id]
      if (!info) return
      graph.addNode(makeNode(n.node_id, info, n.x ?? 80 + (i % 4) * 180, n.y ?? 60 + Math.floor(i / 4) * 110))
    })
    dag.edges?.forEach((e: any) => {
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return
      graph.addEdge({
        source: { cell: String(e.source), port: 'out' },
        target: { cell: String(e.target), port: 'in' },
        attrs: { line: { stroke: '#1677ff', strokeWidth: 1.5, targetMarker: { name: 'block', size: 8 } } },
      })
    })
  }

  const makeNode = (id: number, info: any, x: number, y: number) => ({
    id: String(id),
    x, y, width: 150, height: 44,
    shape: 'rect',
    attrs: {
      body: { rx: 6, ry: 6, fill: '#fff', stroke: TYPE_COLOR[info.node_type] || '#999', strokeWidth: 2 },
      label: { text: info.name.length > 12 ? info.name.slice(0, 12) + '…' : info.name, fill: '#333', fontSize: 13 },
    },
    ports: {
      groups: {
        in: { position: 'left', attrs: { circle: { r: 5, magnet: true, stroke: '#1677ff', fill: '#fff', strokeWidth: 1.5 } } },
        out: { position: 'right', attrs: { circle: { r: 5, magnet: true, stroke: '#1677ff', fill: '#fff', strokeWidth: 1.5 } } },
      },
      items: [{ id: 'in', group: 'in' }, { id: 'out', group: 'out' }],
    },
  })

  const handleAddNode = (id: number) => {
    const graph = graphRef.current
    if (!graph) return
    if (graph.getCellById(String(id))) return
    const info = nodeMap[id]
    if (!info) return
    const n = graph.getNodes().length
    graph.addNode(makeNode(id, info, 80 + (n % 4) * 180, 60 + Math.floor(n / 4) * 110))
    scheduleSync()
  }

  const handleDeleteSelected = () => {
    const graph = graphRef.current
    if (!graph || !selectedCell) return
    const cell = graph.getCellById(selectedCell)
    if (cell) graph.removeCell(cell)
    setSelectedCell(null)
    scheduleSync()
  }

  const canvasEmpty = !(value?.nodes?.length)

  return (
    <div>
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <Select
          placeholder="添加节点到画布"
          options={nodes.map(n => ({
            label: <span><Tag color={TYPE_COLOR[n.node_type]} style={{ fontSize: 11 }}>{n.node_type}</Tag>{n.name}</span>,
            value: n.id,
          }))}
          style={{ width: 220 }}
          onChange={handleAddNode}
          value={null}
          showSearch
          filterOption={(input: string, opt: any) =>
            nodes.find(n => n.id === opt?.value)?.name?.toLowerCase().includes(input.toLowerCase())
          }
        />
        <Button
          danger
          size="small"
          icon={<DeleteOutlined />}
          disabled={!selectedCell}
          onClick={handleDeleteSelected}
        >
          删除选中
        </Button>
        <span style={{ color: '#94a3b8', fontSize: 12, lineHeight: 1.5 }}>
          拖拽节点排版 · 从圆点连线表依赖 · 双击打开脚本
          <Tooltip title="Shift 拖动画布 · Ctrl 滚轮缩放；保存工作流时一并写入布局与依赖">
            <QuestionCircleOutlined style={{ marginLeft: 6, color: '#cbd5e1' }} />
          </Tooltip>
        </span>
      </div>
      <div style={{ position: 'relative' }}>
        <div ref={containerRef} style={{ width: '100%', height: 340, border: '1px solid #e8e8e8', borderRadius: 6, overflow: 'hidden' }} />
        {canvasEmpty && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              pointerEvents: 'none',
              color: '#94a3b8',
              fontSize: 13,
            }}
          >
            从上方选择节点加入画布，再拖拽与连线
          </div>
        )}
      </div>
      <div style={{ marginTop: 6, display: 'flex', gap: 12 }}>
        {Object.entries(TYPE_COLOR).map(([type, color]) => (
          <span key={type} style={{ fontSize: 12, color: '#666', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: color, display: 'inline-block' }} />{type}
          </span>
        ))}
      </div>
    </div>
  )
})

export default DAGEditor

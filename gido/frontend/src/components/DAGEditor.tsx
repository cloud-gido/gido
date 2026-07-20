/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useRef, useCallback, useState, forwardRef, useImperativeHandle, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Select, Button, Tag, Tooltip, message } from 'antd'
import {
  DeleteOutlined,
  QuestionCircleOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  PartitionOutlined,
} from '@ant-design/icons'
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

type DagGraph = { nodes: { node_id: number, x: number, y: number }[], edges: { source: number, target: number }[] }

const TYPE_COLOR: Record<string, string> = {
  SQL: '#1677ff',
  PYTHON: '#52c41a',
  SHELL: '#fa8c16',
  VIRTUAL: '#999',
  SYNC: '#722ed1',
  DEPENDENT: '#eb2f96',
}

const NODE_W = 150
const NODE_H = 44
const GAP_X = 200
const GAP_Y = 100
const ORIGIN_X = 80
const ORIGIN_Y = 60

/** 按依赖分层从左到右排布（拓扑层），同层纵向排列 */
function computeLayeredLayout(
  nodeIds: number[],
  edges: { source: number, target: number }[],
): Map<number, { x: number, y: number }> {
  const idSet = new Set(nodeIds)
  const indeg = new Map<number, number>()
  const outs = new Map<number, number[]>()
  for (const id of nodeIds) {
    indeg.set(id, 0)
    outs.set(id, [])
  }
  for (const e of edges) {
    if (!idSet.has(e.source) || !idSet.has(e.target) || e.source === e.target) continue
    outs.get(e.source)!.push(e.target)
    indeg.set(e.target, (indeg.get(e.target) || 0) + 1)
  }

  const layerOf = new Map<number, number>()
  const queue = nodeIds.filter(id => (indeg.get(id) || 0) === 0)
  for (const id of queue) layerOf.set(id, 0)

  const q = [...queue]
  while (q.length) {
    const u = q.shift()!
    const lu = layerOf.get(u) || 0
    for (const v of outs.get(u) || []) {
      const next = lu + 1
      if (!layerOf.has(v) || (layerOf.get(v) || 0) < next) {
        layerOf.set(v, next)
      }
      const d = (indeg.get(v) || 0) - 1
      indeg.set(v, d)
      if (d === 0) q.push(v)
    }
  }
  for (const id of nodeIds) {
    if (!layerOf.has(id)) layerOf.set(id, 0)
  }

  const layers = new Map<number, number[]>()
  for (const id of nodeIds) {
    const L = layerOf.get(id) || 0
    if (!layers.has(L)) layers.set(L, [])
    layers.get(L)!.push(id)
  }
  for (const ids of layers.values()) ids.sort((a, b) => a - b)

  const pos = new Map<number, { x: number, y: number }>()
  const sortedLayers = [...layers.keys()].sort((a, b) => a - b)
  for (const L of sortedLayers) {
    const ids = layers.get(L) || []
    ids.forEach((id, i) => {
      pos.set(id, { x: ORIGIN_X + L * GAP_X, y: ORIGIN_Y + i * GAP_Y })
    })
  }
  return pos
}

function makeNode(id: number, info: any, x: number, y: number) {
  return {
    id: String(id),
    x, y, width: NODE_W, height: NODE_H,
    shape: 'rect',
    attrs: {
      body: { rx: 6, ry: 6, fill: '#fff', stroke: TYPE_COLOR[info.node_type] || '#999', strokeWidth: 2 },
      label: { text: info.name.length > 12 ? info.name.slice(0, 12) + '…' : info.name, fill: '#333', fontSize: 13 },
    },
    ports: {
      groups: {
        in: {
          position: 'left',
          attrs: {
            circle: { r: 7, magnet: true, stroke: '#1677ff', fill: '#fff', strokeWidth: 1.5 },
          },
        },
        out: {
          position: 'right',
          attrs: {
            circle: { r: 7, magnet: true, stroke: '#1677ff', fill: '#fff', strokeWidth: 1.5 },
          },
        },
      },
      items: [{ id: 'in', group: 'in' }, { id: 'out', group: 'out' }],
    },
  }
}

function loadDAG(graph: Graph, dag: any, nm: Record<number, any>) {
  const nodeIds = new Set((dag.nodes || []).map((n: any) => n.node_id))
  ;(dag.nodes || []).forEach((n: any, i: number) => {
    const info = nm[n.node_id]
    if (!info) return
    graph.addNode(makeNode(n.node_id, info, n.x ?? 80 + (i % 4) * 180, n.y ?? 60 + Math.floor(i / 4) * 110))
  })
  ;(dag.edges || []).forEach((e: any) => {
    if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return
    graph.addEdge({
      source: { cell: String(e.source), port: 'out' },
      target: { cell: String(e.target), port: 'in' },
      attrs: { line: { stroke: '#1677ff', strokeWidth: 1.5, targetMarker: { name: 'block', size: 8 } } },
    })
  })
}

const DAGEditor = forwardRef<DAGEditorRef, DAGEditorProps>(function DAGEditor({ nodes, value, onChange, onNodeDoubleClick }, ref) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)
  const [selectedCell, setSelectedCell] = useState<string | null>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const syncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  const onNodeDoubleClickRef = useRef(onNodeDoubleClick)
  onNodeDoubleClickRef.current = onNodeDoubleClick
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  /** 全屏切换 Portal 会重挂载画布，用快照恢复 */
  const dagSnapRef = useRef<DagGraph | null>(value ? { nodes: value.nodes || [], edges: value.edges || [] } : null)

  const _readDAG = useCallback((): DagGraph => {
    const graph = graphRef.current
    if (!graph) return dagSnapRef.current || { nodes: [], edges: [] }
    const dag: DagGraph = {
      nodes: graph.getNodes().map(n => ({
        node_id: Number(n.id),
        x: Math.round(n.getPosition().x),
        y: Math.round(n.getPosition().y),
      })),
      edges: (() => {
        const seen = new Set<string>()
        const out: { source: number, target: number }[] = []
        for (const e of graph.getEdges()) {
          const source = Number(e.getSourceCellId())
          const target = Number(e.getTargetCellId())
          if (!Number.isFinite(source) || !Number.isFinite(target)) continue
          const key = `${source}->${target}`
          if (seen.has(key)) continue
          seen.add(key)
          out.push({ source, target })
        }
        return out
      })(),
    }
    dagSnapRef.current = dag
    return dag
  }, [])

  useImperativeHandle(ref, () => ({ getDAG: () => _readDAG() }), [_readDAG])

  const scheduleSync = useCallback(() => {
    if (syncTimerRef.current) clearTimeout(syncTimerRef.current)
    syncTimerRef.current = setTimeout(() => {
      onChangeRef.current?.(_readDAG())
    }, 200)
  }, [_readDAG])

  const resizeGraph = useCallback(() => {
    const graph = graphRef.current
    const el = containerRef.current
    const wrap = wrapRef.current
    if (!graph || !el) return
    // X6 会给 container 写死 px 宽高；不能再用 el.clientWidth（会自我锁死变窄）
    const parent = el.parentElement
    const availW = Math.max(
      parent?.clientWidth || 0,
      wrap?.clientWidth || 0,
      320,
    )
    const availH = fullscreen
      ? Math.max(parent?.clientHeight || 0, 240)
      : 340
    el.style.width = `${availW}px`
    el.style.height = `${availH}px`
    graph.resize(availW, availH)
  }, [fullscreen])

  // 全屏时 Portal 到 body，避免被 Modal transform 限制宽度；切换时重建 Graph
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const nodeMap = Object.fromEntries(nodesRef.current.map(n => [n.id, n]))
    const graph = new Graph({
      container: el,
      width: el.clientWidth || 680,
      height: el.clientHeight || 340,
      background: { color: '#fafafa' },
      grid: { visible: true, size: 16 } as any,
      interacting: {
        nodeMovable: true,
        edgeMovable: false,
        magnetConnectable: true,
      },
      connecting: {
        snap: { radius: 28 },
        allowBlank: false,
        allowLoop: false,
        allowMulti: true,
        allowNode: true,
        allowEdge: false,
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
        validateMagnet({ magnet }: any) {
          return magnet?.getAttribute?.('port') === 'out'
        },
        validateConnection({ sourceCell, targetCell, sourceMagnet, targetMagnet }: any) {
          if (!sourceCell || !targetCell || sourceCell.id === targetCell.id) return false
          if (sourceMagnet && sourceMagnet.getAttribute?.('port') !== 'out') return false
          if (targetMagnet) return targetMagnet.getAttribute?.('port') === 'in'
          return typeof targetCell.isNode === 'function' ? targetCell.isNode() : true
        },
      },
      mousewheel: { enabled: true, zoomAtMousePosition: true, modifiers: 'ctrl', minScale: 0.5, maxScale: 2 },
      panning: { enabled: true, modifiers: 'shift' },
    } as any)

    graph.on('node:click', ({ node }: any) => setSelectedCell(node.id))
    graph.on('node:dblclick', ({ node }: any) => onNodeDoubleClickRef.current?.(Number(node.id)))
    graph.on('edge:click', ({ edge }: any) => setSelectedCell(edge.id))
    graph.on('blank:click', () => setSelectedCell(null))
    graph.on('node:change:position', scheduleSync)
    graph.on('edge:connected', scheduleSync)
    graph.on('edge:removed', scheduleSync)
    graph.on('node:removed', scheduleSync)

    graphRef.current = graph
    const snap = dagSnapRef.current
    if (snap?.nodes?.length) loadDAG(graph, snap, nodeMap)

    // Modal 打开动画期间多次对齐父级宽度，避免锁在偏小 px
    const t0 = requestAnimationFrame(() => resizeGraph())
    const t1 = window.setTimeout(() => resizeGraph(), 80)
    const t2 = window.setTimeout(() => resizeGraph(), 320)

    return () => {
      cancelAnimationFrame(t0)
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      if (syncTimerRef.current) clearTimeout(syncTimerRef.current)
      graph.dispose()
      graphRef.current = null
    }
  }, [fullscreen, scheduleSync, resizeGraph])

  const nodeIdKey = JSON.stringify((value?.nodes || []).map((n: any) => n.node_id).sort())
  const nodesMetaKey = JSON.stringify(nodes.map(n => [n.id, n.name, n.node_type]))
  useEffect(() => {
    if (value) {
      dagSnapRef.current = { nodes: value.nodes || [], edges: value.edges || [] }
    }
    const graph = graphRef.current
    if (!graph) return
    const nodeMap = Object.fromEntries(nodesRef.current.map(n => [n.id, n]))
    graph.clearCells()
    if (value?.nodes?.length) loadDAG(graph, value, nodeMap)
  }, [nodeIdKey, nodes.length])

  // 节点改名/类型变更时同步画布标签（不重建边）
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    for (const n of nodes) {
      const cell = graph.getCellById(String(n.id))
      if (!cell || !cell.isNode()) continue
      const label = n.name.length > 12 ? `${n.name.slice(0, 12)}…` : n.name
      cell.attr('label/text', label)
      cell.attr('body/stroke', TYPE_COLOR[n.node_type] || '#999')
    }
  }, [nodesMetaKey])

  useEffect(() => {
    if (fullscreen) {
      const prev = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      return () => { document.body.style.overflow = prev }
    }
  }, [fullscreen])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && fullscreen) {
        dagSnapRef.current = _readDAG()
        setFullscreen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen, _readDAG])

  useEffect(() => {
    const el = wrapRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      // 下一帧再量，避免 Modal 动画中宽高为 0
      requestAnimationFrame(() => resizeGraph())
    })
    ro.observe(el)
    if (el.parentElement) ro.observe(el.parentElement)
    return () => ro.disconnect()
  }, [resizeGraph, fullscreen])

  const handleAddNode = (id: number) => {
    const graph = graphRef.current
    if (!graph) return
    if (graph.getCellById(String(id))) return
    const info = nodesRef.current.find(n => n.id === id)
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

  const handleAutoLayout = () => {
    const graph = graphRef.current
    if (!graph) return
    const dag = _readDAG()
    if (!dag.nodes.length) {
      message.info('画布上还没有节点')
      return
    }
    const ids = dag.nodes.map(n => n.node_id)
    const pos = computeLayeredLayout(ids, dag.edges)
    graph.batchUpdate(() => {
      for (const id of ids) {
        const p = pos.get(id)
        const cell = graph.getCellById(String(id))
        if (p && cell && cell.isNode()) {
          cell.setPosition(p.x, p.y)
        }
      }
    })
    scheduleSync()
    try {
      graph.zoomToFit({ padding: 40, maxScale: 1 })
    } catch {
      /* ignore */
    }
    message.success('已按依赖关系整理布局')
  }

  const toggleFullscreen = () => {
    dagSnapRef.current = _readDAG()
    onChangeRef.current?.(dagSnapRef.current)
    setFullscreen(v => !v)
  }

  const canvasEmpty = !((dagSnapRef.current || value)?.nodes?.length)

  const shellStyle: CSSProperties = fullscreen
    ? {
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        maxWidth: '100vw',
        maxHeight: '100vh',
        zIndex: 2100,
        background: '#fff',
        display: 'flex',
        flexDirection: 'column',
        padding: 16,
        boxSizing: 'border-box',
      }
    : {
        display: 'flex',
        flexDirection: 'column',
        width: '100%',
      }

  const editor = (
    <div ref={wrapRef} style={shellStyle}>
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', flexShrink: 0 }}>
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
          getPopupContainer={() => (fullscreen ? document.body : wrapRef.current || document.body)}
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
        <Button
          size="small"
          icon={<PartitionOutlined />}
          onClick={handleAutoLayout}
          disabled={canvasEmpty}
        >
          整理布局
        </Button>
        <Button
          size="small"
          icon={fullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
          onClick={toggleFullscreen}
        >
          {fullscreen ? '退出全屏' : '全屏'}
        </Button>
        <span style={{ color: '#94a3b8', fontSize: 12, lineHeight: 1.5 }}>
          从右侧圆点拖到目标节点表示依赖 · 双击打开配置弹窗 · 可扇入扇出
          {fullscreen ? ' · Esc 退出全屏' : ''}
          <Tooltip title="Shift 拖动画布 · Ctrl 滚轮缩放；「整理布局」按依赖分层；配置与数据开发共用同一节点与编辑锁">
            <QuestionCircleOutlined style={{ marginLeft: 6, color: '#cbd5e1' }} />
          </Tooltip>
        </span>
      </div>
      <div
        style={{
          position: 'relative',
          flex: 1,
          minHeight: fullscreen ? 0 : 340,
          width: '100%',
          alignSelf: 'stretch',
        }}
      >
        <div
          ref={containerRef}
          style={{
            width: '100%',
            height: fullscreen ? '100%' : 340,
            minHeight: fullscreen ? 0 : 340,
            border: '1px solid #e8e8e8',
            borderRadius: 6,
            overflow: 'hidden',
            boxSizing: 'border-box',
          }}
        />
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
      <div style={{ marginTop: 6, display: 'flex', gap: 12, flexWrap: 'wrap', flexShrink: 0 }}>
        {Object.entries(TYPE_COLOR).map(([type, color]) => (
          <span key={type} style={{ fontSize: 12, color: '#666', display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: color, display: 'inline-block' }} />{type}
          </span>
        ))}
      </div>
    </div>
  )

  return (
    <>
      {/* 全屏时占位，避免 Modal 内容高度塌缩 */}
      {fullscreen ? <div style={{ minHeight: 400 }} aria-hidden /> : null}
      {fullscreen ? createPortal(editor, document.body) : editor}
    </>
  )
})

export default DAGEditor

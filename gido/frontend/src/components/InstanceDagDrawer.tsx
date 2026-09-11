/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Drawer, Alert, Spin, Tag, Space, Button, Descriptions, Empty, message, Tooltip } from 'antd'
import { ReloadOutlined, FileTextOutlined } from '@ant-design/icons'
import { Graph } from '@antv/x6'
import { operationApi } from '../api'
import { formatInTimeZone } from '../utils/datetime'
import { computeLayeredLayout } from './dagEditorOverlay'

/**
 * 实例 DAG：只读地看这次运行的图与每个节点的状态。
 * 图来自实例所属版本的快照，所以改过图之后回看历史实例不会串。
 */
export type InstanceDagTarget = {
  workspaceId: number
  workflowId: number
  instanceId: number
}

const NODE_W = 168
const NODE_H = 48

// 节点状态 → 画布配色。not_run 表示这次运行压根没走到它
const STATUS_STYLE: Record<string, { fill: string; stroke: string; text: string; label: string }> = {
  success: { fill: '#f6ffed', stroke: '#52c41a', text: '#237804', label: '成功' },
  failed: { fill: '#fff1f0', stroke: '#ff4d4f', text: '#a8071a', label: '失败' },
  running: { fill: '#e6f4ff', stroke: '#1677ff', text: '#0958d9', label: '运行中' },
  pending: { fill: '#fffbe6', stroke: '#faad14', text: '#ad6800', label: '等待中' },
  killed: { fill: '#f5f5f5', stroke: '#8c8c8c', text: '#595959', label: '已终止' },
  not_run: { fill: '#fafafa', stroke: '#d9d9d9', text: '#bfbfbf', label: '未运行' },
}

function styleOf(status?: string) {
  return STATUS_STYLE[status || 'not_run'] || STATUS_STYLE.not_run
}

function truncate(s: string, max = 16) {
  return s.length > max ? `${s.slice(0, max)}…` : s
}

function formatDuration(seconds?: number | null) {
  if (seconds == null) return '—'
  const s = Math.max(0, Number(seconds))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

export default function InstanceDagDrawer({
  target,
  displayTz,
  onClose,
  onChanged,
}: {
  target: InstanceDagTarget | null
  displayTz: string
  onClose: () => void
  onChanged?: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState<any>(null)
  const [selected, setSelected] = useState<any>(null)
  const [logOpen, setLogOpen] = useState(false)
  const [logContent, setLogContent] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const graphRef = useRef<Graph | null>(null)

  const load = useCallback(async () => {
    if (!target) return
    setLoading(true)
    setError('')
    try {
      const res: any = await operationApi.instanceDag(target.workspaceId, target.workflowId, target.instanceId)
      setData(res)
      setSelected(null)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '加载实例 DAG 失败')
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [target?.workspaceId, target?.workflowId, target?.instanceId])

  useEffect(() => {
    if (!target) {
      setData(null)
      setSelected(null)
      setError('')
      return
    }
    load()
  }, [target?.instanceId, load])

  // 抽屉里的画布要等容器有尺寸才能建，所以依赖 data 而不是 target
  useEffect(() => {
    const el = containerRef.current
    if (!el || !data) return undefined
    const graph = new Graph({
      container: el,
      width: el.clientWidth || 760,
      height: el.clientHeight || 420,
      background: { color: '#fafafa' },
      grid: { visible: true, size: 16 } as any,
      // 只读视图：不能连线、不能拖动节点，避免运维误以为能在这里改编排
      interacting: false,
      mousewheel: { enabled: true, zoomAtMousePosition: true, modifiers: 'ctrl', minScale: 0.4, maxScale: 2 },
      panning: { enabled: true },
    } as any)
    graphRef.current = graph

    const nodes = data.nodes || []
    const edges = data.edges || []
    const layout = computeLayeredLayout(
      nodes.map((n: any) => Number(n.node_id)),
      edges,
    )
    for (const n of nodes) {
      const pos = layout.get(Number(n.node_id)) || { x: 40, y: 40 }
      const st = styleOf(n.status)
      graph.addNode({
        id: String(n.node_id),
        x: pos.x,
        y: pos.y,
        width: NODE_W,
        height: NODE_H,
        attrs: {
          body: { fill: st.fill, stroke: st.stroke, strokeWidth: 1.5, rx: 6, ry: 6 },
          label: {
            text: `${truncate(n.name || '')}\n${st.label}${n.duration_seconds != null ? ` · ${formatDuration(n.duration_seconds)}` : ''}`,
            fill: st.text,
            fontSize: 12,
            lineHeight: 16,
          },
        },
        data: n,
      })
    }
    for (const e of edges) {
      graph.addEdge({
        source: String(e.source),
        target: String(e.target),
        router: { name: 'er', args: { direction: 'H' } },
        connector: 'rounded',
        attrs: { line: { stroke: '#bfbfbf', strokeWidth: 1.5, targetMarker: { name: 'block', size: 8 } } },
      })
    }
    graph.on('node:click', ({ node }) => {
      setSelected(typeof node.getData === 'function' ? node.getData() : null)
    })
    graph.zoomToFit({ padding: 24, maxScale: 1 })

    return () => {
      graph.dispose()
      graphRef.current = null
    }
  }, [data])

  const showLog = async (nodeInstanceId?: number | null) => {
    if (!nodeInstanceId) {
      message.info('这个节点这次没有运行记录，没有日志')
      return
    }
    const res: any = await operationApi.getLog(nodeInstanceId)
    setLogContent(res?.log_content || res?.log || '（无日志内容）')
    setLogOpen(true)
  }

  const retryNode = async () => {
    if (!target || !selected?.node_instance_id) return
    try {
      await operationApi.retry(selected.node_instance_id)
      message.success('已提交节点重试')
      onChanged?.()
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '节点重试失败')
    }
  }

  const inst = data?.instance

  return (
    <Drawer
      title={inst ? `实例 #${inst.id} 运行图 · ${inst.workflow_name || ''}` : '实例运行图'}
      open={!!target}
      onClose={onClose}
      width={1000}
      extra={<Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>}
    >
      {loading && !data ? (
        <Spin />
      ) : error ? (
        <Alert type="error" showIcon message={error} />
      ) : data ? (
        <>
          <Descriptions size="small" column={4} style={{ marginBottom: 12 }}>
            <Descriptions.Item label="状态">
              <Tag color={styleOf(inst.status).stroke}>{inst.status}</Tag>
              {inst.status_override ? <Tag color="purple">人工</Tag> : null}
            </Descriptions.Item>
            <Descriptions.Item label="业务日期">{inst.business_date || '—'}</Descriptions.Item>
            <Descriptions.Item label="开始">{formatInTimeZone(inst.started_at, displayTz)}</Descriptions.Item>
            <Descriptions.Item label="结束">{formatInTimeZone(inst.finished_at, displayTz)}</Descriptions.Item>
          </Descriptions>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              inst.version_no
                ? `这是发布版本 v${inst.version_no} 当时的图，不是当前编排`
                : '这是运行时的图，不是当前编排'
            }
            description="点节点看详情与日志。Ctrl+滚轮缩放，拖动平移。「未运行」是快照里有这个节点但这次没走到它。"
          />
          <div style={{ display: 'flex', gap: 12 }}>
            <div
              ref={containerRef}
              style={{ flex: 1, height: 440, border: '1px solid #f0f0f0', borderRadius: 6 }}
            />
            <div style={{ width: 300 }}>
              {selected ? (
                <>
                  <Descriptions size="small" column={1} title={selected.name} style={{ marginBottom: 12 }}>
                    <Descriptions.Item label="类型">
                      <Tag>{selected.node_type}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="状态">
                      <Tag color={styleOf(selected.status).stroke}>{styleOf(selected.status).label}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="耗时">{formatDuration(selected.duration_seconds)}</Descriptions.Item>
                    <Descriptions.Item label="开始">{formatInTimeZone(selected.started_at, displayTz)}</Descriptions.Item>
                    <Descriptions.Item label="结束">{formatInTimeZone(selected.finished_at, displayTz)}</Descriptions.Item>
                    <Descriptions.Item label="重试次数">{selected.retry_count ?? '—'}</Descriptions.Item>
                    {selected.current_name && selected.current_name !== selected.name ? (
                      <Descriptions.Item label="现用名">
                        <Tooltip title="发布后改过名字。图上显示的是发布时的名字，与引擎日志一致">
                          <span>{selected.current_name}</span>
                        </Tooltip>
                      </Descriptions.Item>
                    ) : null}
                  </Descriptions>
                  {selected.log_summary ? (
                    <Alert
                      type={selected.status === 'failed' ? 'error' : 'info'}
                      style={{ marginBottom: 12 }}
                      message={selected.log_summary}
                    />
                  ) : null}
                  <Space wrap>
                    <Button
                      size="small"
                      icon={<FileTextOutlined />}
                      onClick={() => showLog(selected.node_instance_id)}
                    >
                      日志
                    </Button>
                    {selected.status === 'failed' && selected.node_instance_id ? (
                      <Button size="small" onClick={retryNode}>重试本节点</Button>
                    ) : null}
                  </Space>
                </>
              ) : (
                <Empty description="点一个节点看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          </div>
          <Drawer
            title="运行日志"
            open={logOpen}
            onClose={() => setLogOpen(false)}
            width={700}
          >
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{logContent}</pre>
          </Drawer>
        </>
      ) : (
        <Empty description="暂无数据" />
      )}
    </Drawer>
  )
}

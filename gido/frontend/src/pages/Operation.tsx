/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, type ReactNode } from 'react'
import { Table, Tag, Button, Space, Drawer, Row, Col, Statistic, Select, message, Alert, Tooltip, Card, Descriptions } from 'antd'
import { ReloadOutlined, StopOutlined, FileTextOutlined, UnorderedListOutlined, AuditOutlined } from '@ant-design/icons'
import { useNavigate, Link } from 'react-router-dom'
import { operationApi, schedulerApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'
import OpsDashboardCharts from '../components/OpsDashboardCharts'
import { R } from '../routes'
import { isWorkspaceAdmin } from '../perm'

const STATUS_COLOR: Record<string, string> = {
  success: 'green', failed: 'red', running: 'blue', pending: 'orange', killed: 'default'
}

type ListMode = 'nodes' | 'workflows'

export default function OperationPage() {
  const { currentWorkspace, user } = useAppStore()
  const navigate = useNavigate()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [overview, setOverview] = useState<any>({})
  const [instances, setInstances] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [logDrawer, setLogDrawer] = useState(false)
  const [logContent, setLogContent] = useState('')
  const [activeLogNodeId, setActiveLogNodeId] = useState<number | undefined>()
  const [logExtras, setLogExtras] = useState<{
    hint?: string
    schedulerId?: string | number | null
    schedulerUrl?: string | null
  }>({})
  const [page, setPage] = useState(1)
  const [syncingSchedulerMeta, setSyncingSchedulerMeta] = useState(false)
  /** 概览数字为工作流实例级；与下方节点表切换 */
  const [listMode, setListMode] = useState<ListMode>('workflows')
  /** 与概览「今日实例」一致：仅 created_at 为当日(UTC)的工作流实例 */
  const [todayOnlyWorkflows, setTodayOnlyWorkflows] = useState(false)
  /** 节点表：仅某工作流实例 */
  const [workflowInstanceScope, setWorkflowInstanceScope] = useState<number | undefined>()
  const [drilldownContext, setDrilldownContext] = useState<any>(null)

  const load = async () => {
    if (!wsId) return
    const ov: any = await operationApi.overview(wsId)
    setOverview(ov)
    let inst: any
    if (listMode === 'workflows') {
      inst = await operationApi.instances(wsId, {
        page,
        page_size: 20,
        status: statusFilter || undefined,
        today_only: todayOnlyWorkflows ? true : undefined,
      })
      setDrilldownContext(null)
    } else {
      const nparams: Record<string, unknown> = {
        status: statusFilter,
        page,
        page_size: 20,
      }
      if (workflowInstanceScope != null) {
        nparams.workflow_instance_id = workflowInstanceScope
      }
      inst = await operationApi.nodeInstances(wsId, nparams)
      setDrilldownContext(inst.context || null)
    }
    setInstances(inst.items)
    setTotal(inst.total)
  }

  useEffect(() => { load() }, [wsId, statusFilter, page, listMode, todayOnlyWorkflows, workflowInstanceScope])

  const showLog = async (niId: number) => {
    const res: any = await operationApi.getLog(niId)
    setActiveLogNodeId(niId)
    setLogContent(res.log || '暂无日志')
    setLogExtras({
      hint: res.log_source_hint,
      schedulerId: res.scheduler_instance_id ?? res.dolphin_process_instance_id ?? null,
      schedulerUrl: res.dolphin_process_instance_url ?? null,
    })
    setLogDrawer(true)
  }

  const handleKill = async (niId: number) => {
    await operationApi.kill(niId)
    message.success('已终止')
    load()
  }

  const handleRetry = async (niId: number) => {
    await operationApi.retry(niId)
    message.success('已提交重试')
    load()
  }

  const handleWorkflowStop = async (row: any) => {
    if (!wsId || !row.workflow_id) return
    await operationApi.stopWorkflowInstance(wsId, row.workflow_id, row.id)
    message.success('已停止工作流实例')
    load()
  }

  const handleWorkflowRefresh = async (row: any) => {
    if (!wsId || !row.workflow_id) return
    await operationApi.refreshWorkflowInstance(wsId, row.workflow_id, row.id)
    message.success('实例状态已刷新')
    load()
  }

  const handleWorkflowRerun = async (row: any) => {
    if (!wsId || !row.workflow_id) return
    await operationApi.rerunWorkflowInstance(wsId, row.workflow_id, row.id)
    message.success('已提交重跑')
    load()
  }

  const handleRetryFailedNodes = async (row: any) => {
    if (!wsId || !row.workflow_id) return
    await operationApi.retryFailedNodes(wsId, row.workflow_id, row.id)
    message.success('已提交失败节点重试')
    load()
  }

  /** 从调度引擎拉取实例元数据，区分「定时调度」与「手动/API」等 */
  const handleSyncSchedulerMeta = async () => {
    setSyncingSchedulerMeta(true)
    try {
      const res: any = await schedulerApi.syncDolphinInstances()
      message.success(
        `调度实例已同步：扫描流程定义 ${res?.definitions_scanned ?? 0} 个，新入库实例 ${res?.ingested ?? 0}，` +
          `节点行 ${res?.node_rows_touched ?? 0}，commandType 更新 ${res?.command_types_filled ?? 0}，` +
          `运行中→结束 ${res?.synced ?? 0} 条（详情补全检查了 ${res?.checked ?? 0} 条）`
      )
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '同步失败（请确认生产调度已配置且 Token 有效）')
    } finally {
      setSyncingSchedulerMeta(false)
    }
  }

  /** 点击概览数字：切到与统计同源的工作流实例列表 */
  const drillFromOverview = (kind: 'today' | 'running' | 'success' | 'failed') => {
    setListMode('workflows')
    setWorkflowInstanceScope(undefined)
    setPage(1)
    if (kind === 'today') {
      setTodayOnlyWorkflows(true)
      setStatusFilter(undefined)
    } else {
      setTodayOnlyWorkflows(false)
      setStatusFilter(kind)
    }
  }

  const backToWorkflowList = () => {
    setListMode('workflows')
    setTodayOnlyWorkflows(false)
    setWorkflowInstanceScope(undefined)
    setDrilldownContext(null)
    setStatusFilter(undefined)
    setPage(1)
  }

  const openNodesForWorkflowInstance = (wfInstId: number) => {
    setListMode('nodes')
    setWorkflowInstanceScope(wfInstId)
    setTodayOnlyWorkflows(false)
    setStatusFilter(undefined)
    setPage(1)
  }

  const clickableStat = (inner: ReactNode, onClick: () => void, tip: string) => (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={e => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      title={tip}
      style={{ cursor: 'pointer', borderRadius: 8, padding: '4px 0' }}
    >
      {inner}
    </div>
  )

  const formatDuration = (seconds?: number | null) => {
    if (seconds == null || Number.isNaN(Number(seconds))) return '—'
    const s = Math.max(0, Number(seconds))
    if (s < 60) return `${s}s`
    const m = Math.floor(s / 60)
    const rest = s % 60
    if (m < 60) return `${m}m ${rest}s`
    const h = Math.floor(m / 60)
    return `${h}h ${m % 60}m`
  }

  const nodeColumns = [
    { title: '节点实例', dataIndex: 'id', width: 88 },
    { title: '工作流实例', dataIndex: 'workflow_instance_id', width: 110 },
    { title: '工作流', dataIndex: 'workflow_name', width: 140, ellipsis: true },
    {
      title: '触发来源',
      dataIndex: 'trigger_label',
      width: 220,
      ellipsis: true,
      render: (label: string, row: any) => (
        <Tooltip
          title={
            [
              row.dolphin_command_type && `调度 commandType: ${row.dolphin_command_type}`,
              row.scheduler_instance_id && `scheduler_instance_id: ${row.scheduler_instance_id}`,
              row.scheduler_task_instance_id && `scheduler_task_instance_id: ${row.scheduler_task_instance_id}`,
              row.scheduler_task_code && `scheduler_task_code: ${row.scheduler_task_code}`,
              row.trigger_type && `trigger_type: ${row.trigger_type}`,
            ]
              .filter(Boolean)
              .join('\n') || undefined
          }
        >
          <span>{label || row.trigger_type || '—'}</span>
        </Tooltip>
      ),
    },
    { title: '节点名称', dataIndex: 'node_name' },
    { title: '类型', dataIndex: 'node_type', render: (t: string) => <Tag>{t}</Tag> },
    { title: '状态', dataIndex: 'status', render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    {
      title: '失败原因 / 摘要',
      dataIndex: 'log_content',
      width: 220,
      ellipsis: true,
      render: (_: string, row: any) => {
        const text = String(row.log_summary || row.error_summary || row.log_content || '').trim()
        return text ? <Tooltip title={text}><span>{text.slice(0, 80)}</span></Tooltip> : <span style={{ color: '#bbb' }}>—</span>
      },
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '结束时间',
      dataIndex: 'finished_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    { title: '重试次数', dataIndex: 'retry_count' },
    {
      title: '操作', render: (_: any, row: any) => (
        <Space>
          <Button size="small" icon={<FileTextOutlined />} onClick={() => showLog(row.id)}>日志</Button>
          {row.status === 'running' && <Button size="small" danger icon={<StopOutlined />} onClick={() => handleKill(row.id)}>终止</Button>}
          {row.status === 'failed' && <Button size="small" icon={<ReloadOutlined />} onClick={() => handleRetry(row.id)}>重试</Button>}
        </Space>
      )
    }
  ]

  const workflowColumns = [
    { title: '工作流实例', dataIndex: 'id', width: 110 },
    { title: '工作流', dataIndex: 'workflow_name', width: 160, ellipsis: true },
    { title: '状态', dataIndex: 'status', width: 100, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    {
      title: '触发来源',
      dataIndex: 'trigger_label',
      width: 220,
      ellipsis: true,
      render: (label: string, row: any) => (
        <Tooltip
          title={
            [
              row.dolphin_command_type && `调度 commandType: ${row.dolphin_command_type}`,
              row.scheduler_instance_id && `scheduler_instance_id: ${row.scheduler_instance_id}`,
              row.trigger_type && `trigger_type: ${row.trigger_type}`,
            ]
              .filter(Boolean)
              .join('\n') || undefined
          }
        >
          <span>{label || row.trigger_type || '—'}</span>
        </Tooltip>
      ),
    },
    { title: '业务日期', dataIndex: 'business_date', width: 110 },
    {
      title: '节点进度',
      width: 160,
      render: (_: unknown, row: any) => (
        <Space size={4} wrap>
          <Tag>总 {row.node_total ?? 0}</Tag>
          {(row.running_node_count ?? 0) > 0 && <Tag color="blue">运行 {row.running_node_count}</Tag>}
          {(row.failed_node_count ?? 0) > 0 && <Tag color="red">失败 {row.failed_node_count}</Tag>}
        </Space>
      ),
    },
    {
      title: '当前 / 失败节点',
      width: 220,
      ellipsis: true,
      render: (_: unknown, row: any) => {
        const failed = Array.isArray(row.failed_nodes) ? row.failed_nodes : []
        const current = Array.isArray(row.current_nodes) ? row.current_nodes : []
        const text = failed.length ? `失败：${failed.join('、')}` : current.length ? `当前：${current.join('、')}` : '—'
        return text === '—' ? <span style={{ color: '#bbb' }}>—</span> : <Tooltip title={text}><span>{text}</span></Tooltip>
      },
    },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '结束时间',
      dataIndex: 'finished_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    { title: '耗时', dataIndex: 'duration_seconds', width: 90, render: (v: number) => formatDuration(v) },
    {
      title: '操作',
      width: 300,
      render: (_: unknown, row: any) => (
        <Space>
          <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => openNodesForWorkflowInstance(row.id)}>
            节点明细
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => handleWorkflowRefresh(row)}>
            刷新
          </Button>
          {row.status === 'running' && (
            <Button size="small" danger icon={<StopOutlined />} onClick={() => handleWorkflowStop(row)}>
              停止
            </Button>
          )}
          {['failed', 'killed', 'success'].includes(row.status) && (
            <Button size="small" onClick={() => handleWorkflowRerun(row)}>
              重跑
            </Button>
          )}
          {row.status === 'failed' && (
            <Button size="small" onClick={() => handleRetryFailedNodes(row)}>
              重试失败节点
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const tableTitle =
    listMode === 'workflows'
      ? `工作流实例列表${todayOnlyWorkflows ? '（今日创建）' : ''}${statusFilter ? `（状态：${statusFilter}）` : ''}`
      : `工作流实例 #${workflowInstanceScope} 的节点明细`
  const activeNodeColumns = workflowInstanceScope != null
    ? nodeColumns.filter((c: any) => !['工作流实例', '工作流', '触发来源'].includes(String(c.title)))
    : nodeColumns

  return (
    <div>
      <h2>实例中心</h2>
      <Alert
        type="info"
        showIcon
        closable
        style={{ marginBottom: 16 }}
        message="仅展示已上线工作流的生产运行实例"
        description={
          <span>
            调度、补数与运维重跑请在本页查看。数据开发试跑与数据探查查询请到{' '}
            <Link to={R.batch.runHistory}>运行历史</Link>。
          </span>
        }
      />
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={4}>
          {clickableStat(
            <Statistic title="今日实例" value={overview.today_instances || 0} />,
            () => drillFromOverview('today'),
            '今日创建的工作流实例（created_at，UTC 日界线）'
          )}
        </Col>
        <Col span={4}>
          {clickableStat(
            <Statistic title="运行中" value={overview.running || 0} valueStyle={{ color: '#1677ff' }} />,
            () => drillFromOverview('running'),
            '状态为 running 的工作流实例'
          )}
        </Col>
        <Col span={4}>
          {clickableStat(
            <Statistic title="成功" value={overview.success || 0} valueStyle={{ color: '#52c41a' }} />,
            () => drillFromOverview('success'),
            '状态为 success 的工作流实例'
          )}
        </Col>
        <Col span={4}>
          {clickableStat(
            <Statistic title="失败" value={overview.failed || 0} valueStyle={{ color: '#ff4d4f' }} />,
            () => drillFromOverview('failed'),
            '状态为 failed 的工作流实例'
          )}
        </Col>
        <Col span={4}>
          <Statistic title="成功率" value={overview.success_rate || 'N/A'} />
        </Col>
        {(overview.pending_approvals ?? 0) > 0 && isWorkspaceAdmin(user, currentWorkspace) && (
          <Col span={4}>
            {clickableStat(
              <Statistic
                title="待审批发布"
                value={overview.pending_approvals}
                valueStyle={{ color: '#fa8c16' }}
                prefix={<AuditOutlined />}
              />,
              () => navigate(R.batch.approval),
              '前往发布审批',
            )}
          </Col>
        )}
      </Row>

      <OpsDashboardCharts
        dailyTrend={overview.daily_trend}
        statusDistribution={overview.status_distribution}
      />

      {listMode === 'nodes' && drilldownContext && (
        <Card size="small" style={{ marginBottom: 12 }}>
          <Descriptions size="small" column={4}>
            <Descriptions.Item label="工作流">{drilldownContext.workflow_name}</Descriptions.Item>
            <Descriptions.Item label="实例">#{drilldownContext.workflow_instance_id}</Descriptions.Item>
            <Descriptions.Item label="状态"><Tag color={STATUS_COLOR[drilldownContext.status]}>{drilldownContext.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="业务日期">{drilldownContext.business_date || '—'}</Descriptions.Item>
            <Descriptions.Item label="触发来源">{drilldownContext.trigger_label || drilldownContext.trigger_type || '—'}</Descriptions.Item>
            <Descriptions.Item label="开始时间">{formatInTimeZone(drilldownContext.started_at, displayTz)}</Descriptions.Item>
            <Descriptions.Item label="结束时间">{formatInTimeZone(drilldownContext.finished_at, displayTz)}</Descriptions.Item>
            <Descriptions.Item label="最近同步">{formatInTimeZone(drilldownContext.last_synced_at, displayTz)}</Descriptions.Item>
          </Descriptions>
          <div style={{ marginTop: 8 }}>
            <Space size={6} wrap>
              {(drilldownContext.node_status_distribution || []).map((x: any) => (
                <Tag key={x.status} color={STATUS_COLOR[x.status] || 'default'}>{x.status}: {x.count}</Tag>
              ))}
              {drilldownContext.scheduler_error ? <Tag color="red">同步异常</Tag> : null}
            </Space>
            {drilldownContext.scheduler_error ? (
              <div style={{ marginTop: 6, color: '#ff4d4f' }}>{drilldownContext.scheduler_error}</div>
            ) : null}
          </div>
        </Card>
      )}

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        {listMode === 'nodes' && (
          <Button type="primary" ghost onClick={backToWorkflowList}>
            返回工作流实例
          </Button>
        )}
        <Select
          placeholder="状态筛选"
          allowClear
          style={{ width: 160 }}
          value={statusFilter}
          onChange={v => { setStatusFilter(v); setPage(1) }}
          options={['running', 'success', 'failed', 'pending', 'killed'].map(s => ({ label: s, value: s }))}
        />
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Button loading={syncingSchedulerMeta} onClick={handleSyncSchedulerMeta}>同步调度实例</Button>
      </div>

      <div style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>{tableTitle}</div>
      <Table
        dataSource={instances}
        columns={listMode === 'workflows' ? workflowColumns : activeNodeColumns}
        rowKey="id"
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
      />

      <Drawer
        title="运行日志"
        open={logDrawer}
        onClose={() => setLogDrawer(false)}
        width={700}
        extra={activeLogNodeId ? <Button icon={<ReloadOutlined />} onClick={() => showLog(activeLogNodeId)}>刷新日志</Button> : null}
      >
        {(logExtras.hint || logExtras.schedulerUrl != null || logExtras.schedulerId != null) && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <div>
                {logExtras.hint && <div style={{ marginBottom: 8 }}>{logExtras.hint}</div>}
                {logExtras.schedulerId != null && logExtras.schedulerId !== undefined && (
                  <div style={{ marginBottom: logExtras.schedulerUrl ? 8 : 0 }}>
                    调度实例 ID：<code>{logExtras.schedulerId}</code>
                  </div>
                )}
                {logExtras.schedulerUrl ? (
                  <a href={logExtras.schedulerUrl} target="_blank" rel="noreferrer">
                    打开调度引擎诊断页
                  </a>
                ) : null}
              </div>
            }
          />
        )}
        <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: 16, borderRadius: 4, minHeight: 400, whiteSpace: 'pre-wrap', fontSize: 13 }}>
          {logContent}
        </pre>
      </Drawer>
    </div>
  )
}

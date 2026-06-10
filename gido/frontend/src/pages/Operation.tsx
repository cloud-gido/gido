/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, type ReactNode } from 'react'
import { Table, Tag, Button, Space, Drawer, Row, Col, Statistic, Select, message, Alert, Switch, Tooltip } from 'antd'
import { ReloadOutlined, StopOutlined, FileTextOutlined, UnorderedListOutlined, AuditOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
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
  const [logExtras, setLogExtras] = useState<{
    hint?: string
    dolphinId?: number | null
    dolphinUrl?: string | null
  }>({})
  const [page, setPage] = useState(1)
  /** 默认 false：仅工作流实例；开启后与旧版一致包含 Studio 单节点试跑，便于回归对照 */
  const [includeStudioRuns, setIncludeStudioRuns] = useState(false)
  const [syncingDsMeta, setSyncingDsMeta] = useState(false)
  /** 概览数字为工作流实例级；与下方节点表切换 */
  const [listMode, setListMode] = useState<ListMode>('nodes')
  /** 与概览「今日实例」一致：仅 created_at 为当日(UTC)的工作流实例 */
  const [todayOnlyWorkflows, setTodayOnlyWorkflows] = useState(false)
  /** 节点表：仅某工作流实例 */
  const [workflowInstanceScope, setWorkflowInstanceScope] = useState<number | undefined>()
  /** 默认 false：不展示数据开发「立即运行」产生的 Dolphin 启动类实例，与调度运维分离 */
  const [includeManualDevRuns, setIncludeManualDevRuns] = useState(false)

  const load = async () => {
    if (!wsId) return
    const manualParam = { include_manual_development_runs: includeManualDevRuns }
    const ov: any = await operationApi.overview(wsId, manualParam)
    setOverview(ov)
    let inst: any
    if (listMode === 'workflows') {
      inst = await operationApi.instances(wsId, {
        page,
        page_size: 20,
        status: statusFilter || undefined,
        today_only: todayOnlyWorkflows ? true : undefined,
        ...manualParam,
      })
    } else {
      const nparams: Record<string, unknown> = {
        status: statusFilter,
        page,
        page_size: 20,
        include_studio_runs: includeStudioRuns,
        ...manualParam,
      }
      if (workflowInstanceScope != null) {
        nparams.workflow_instance_id = workflowInstanceScope
      }
      inst = await operationApi.nodeInstances(wsId, nparams)
    }
    setInstances(inst.items)
    setTotal(inst.total)
  }

  useEffect(() => { load() }, [wsId, statusFilter, page, includeStudioRuns, listMode, todayOnlyWorkflows, workflowInstanceScope, includeManualDevRuns])

  const showLog = async (niId: number) => {
    const res: any = await operationApi.getLog(niId)
    setLogContent(res.log || '暂无日志')
    setLogExtras({
      hint: res.log_source_hint,
      dolphinId: res.dolphin_process_instance_id ?? null,
      dolphinUrl: res.dolphin_process_instance_url ?? null,
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

  /** 从 Dolphin 拉取流程实例 commandType，区分「定时调度」与「手动/API」等（需 DS 已启用） */
  const handleSyncDolphinTriggerMeta = async () => {
    setSyncingDsMeta(true)
    try {
      const res: any = await schedulerApi.syncDolphinInstances()
      message.success(
        `Dolphin 已同步：扫描流程定义 ${res?.definitions_scanned ?? 0} 个，新入库实例 ${res?.ingested ?? 0}，` +
          `节点行 ${res?.node_rows_touched ?? 0}，commandType 更新 ${res?.command_types_filled ?? 0}，` +
          `运行中→结束 ${res?.synced ?? 0} 条（详情补全检查了 ${res?.checked ?? 0} 条）`
      )
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '同步失败（请确认 DS 已配置且 Token 有效）')
    } finally {
      setSyncingDsMeta(false)
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

  const backToNodeList = () => {
    setListMode('nodes')
    setTodayOnlyWorkflows(false)
    setWorkflowInstanceScope(undefined)
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

  const nodeColumns = [
    { title: '节点实例', dataIndex: 'id', width: 88 },
    { title: '工作流实例', dataIndex: 'workflow_instance_id', width: 110 },
    { title: '工作流', dataIndex: 'workflow_name', width: 140, ellipsis: true },
    {
      title: '触发 / Dolphin',
      dataIndex: 'trigger_label',
      width: 220,
      ellipsis: true,
      render: (label: string, row: any) => (
        <Tooltip
          title={
            [row.dolphin_command_type && `Dolphin commandType: ${row.dolphin_command_type}`, row.trigger_type && `trigger_type: ${row.trigger_type}`]
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
      title: '触发 / Dolphin',
      dataIndex: 'trigger_label',
      width: 220,
      ellipsis: true,
      render: (label: string, row: any) => (
        <Tooltip
          title={
            [row.dolphin_command_type && `Dolphin commandType: ${row.dolphin_command_type}`, row.trigger_type && `trigger_type: ${row.trigger_type}`]
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
      title: '开始时间',
      dataIndex: 'started_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '结束时间',
      dataIndex: 'finished_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '操作',
      width: 120,
      render: (_: unknown, row: any) => (
        <Button type="link" size="small" icon={<UnorderedListOutlined />} onClick={() => openNodesForWorkflowInstance(row.id)}>
          节点明细
        </Button>
      ),
    },
  ]

  const tableTitle =
    listMode === 'workflows'
      ? `工作流实例列表${todayOnlyWorkflows ? '（今日创建）' : ''}${statusFilter ? `（状态：${statusFilter}）` : ''}`
      : `节点实例列表${workflowInstanceScope != null ? `（工作流实例 #${workflowInstanceScope}）` : ''}`

  return (
    <div>
      <h2>运维中心</h2>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={
          includeStudioRuns
            ? '当前已开启「包含开发试跑」，列表与旧版一致会混入 Studio 单节点运行记录；生产环境建议关闭以便运维聚焦工作流实例。'
            : '默认仅展示「工作流提交/调度」产生的实例，不包含数据开发中的单节点试跑；与周期调度运维、开发试跑分离一致。需要对照旧行为时可打开下方开关。'
        }
        description={
          <div>
            <div style={{ marginBottom: 8 }}>
              顶部统计与列表默认<strong>不包含</strong>数据开发里「立即运行」产生的实例（Dolphin 上为启动工作流、无调度时间，与周期调度运维区分）。需要排查时可打开「显示开发立即运行」。列表按<strong>开始时间从新到旧</strong>排序。
            </div>
            <div style={{ marginBottom: 8 }}>
              「今日实例」等按<strong>工作流实例</strong>计数；点击数字打开同源工作流列表。默认表为<strong>节点实例</strong>，可从工作流列表点「节点明细」下钻。
            </div>
            <div style={{ marginBottom: 8 }}>
              已发布到 Dolphin 的工作流可使用「同步 Dolphin 触发类型」，或由调度定时请求接口 <code>POST /scheduler/ds/sync-instances</code>，把 Dolphin 上的运行写回库中。升级后若个别实例时间与 Dolphin 不一致，请再同步一次（此前拉取列表时未按流程定义过滤会导致混入其它工作流实例）。
            </div>
            <div>
              从工作流实例下钻到节点明细时，会自动向 Dolphin 拉取该实例的流程与任务时间，便于与 Dolphin「工作流实例 / 任务实例」对照。
            </div>
          </div>
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

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        {listMode === 'workflows' && (
          <Button type="primary" ghost onClick={backToNodeList}>
            返回节点明细
          </Button>
        )}
        {listMode === 'nodes' && workflowInstanceScope != null && (
          <Button type="link" onClick={() => { setWorkflowInstanceScope(undefined); setPage(1) }}>
            清除工作流实例筛选
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
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Switch checked={includeManualDevRuns} onChange={v => { setIncludeManualDevRuns(v); setPage(1) }} />
          显示开发立即运行
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <Switch
            checked={includeStudioRuns}
            disabled={listMode === 'workflows'}
            onChange={v => { setIncludeStudioRuns(v); setPage(1) }}
          />
          <span style={{ color: listMode === 'workflows' ? '#999' : undefined }}>包含开发试跑（回归对照）</span>
        </span>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Button loading={syncingDsMeta} onClick={handleSyncDolphinTriggerMeta}>同步 Dolphin 触发类型</Button>
      </div>

      <div style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>{tableTitle}</div>
      <Table
        dataSource={instances}
        columns={listMode === 'workflows' ? workflowColumns : nodeColumns}
        rowKey="id"
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
      />

      <Drawer title="运行日志" open={logDrawer} onClose={() => setLogDrawer(false)} width={700}>
        {(logExtras.hint || logExtras.dolphinUrl != null || logExtras.dolphinId != null) && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <div>
                {logExtras.hint && <div style={{ marginBottom: 8 }}>{logExtras.hint}</div>}
                {logExtras.dolphinId != null && logExtras.dolphinId !== undefined && (
                  <div style={{ marginBottom: logExtras.dolphinUrl ? 8 : 0 }}>
                    Dolphin 流程实例 ID：<code>{logExtras.dolphinId}</code>
                  </div>
                )}
                {logExtras.dolphinUrl ? (
                  <a href={logExtras.dolphinUrl} target="_blank" rel="noreferrer">
                    在 Dolphin 中打开该流程实例
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

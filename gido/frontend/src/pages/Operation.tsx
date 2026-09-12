/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, type ReactNode } from 'react'
import { Table, Tag, Button, Space, Row, Col, Statistic, Select, message, Alert, Tooltip, Card, Switch, Tabs, Modal, Input, Dropdown, Collapse, Descriptions, type MenuProps } from 'antd'
import { ReloadOutlined, StopOutlined, AuditOutlined, CheckCircleOutlined, QuestionCircleOutlined, PartitionOutlined, DownOutlined } from '@ant-design/icons'
import { useNavigate, Link, useSearchParams } from 'react-router-dom'
import { operationApi, schedulerApi, workflowApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone, formatInTimeZoneCompact } from '../utils/datetime'
import { describePollError } from '../utils/pollError'
import OpsDashboardCharts from '../components/OpsDashboardCharts'
import RunCollectorStatus from '../components/RunCollectorStatus'
import RunDiagnosisDrawer, { type DiagnosisTarget } from '../components/RunDiagnosisDrawer'
import InstanceDagDrawer, { type InstanceDagTarget } from '../components/InstanceDagDrawer'
import SoftRowDetailToggle from '../components/SoftRowDetailToggle'
import { useSoftExpandedRows } from '../hooks/useSoftExpandedRows'
import { R } from '../routes'
import { isPlatformAdmin, isWorkspaceAdmin } from '../perm'

const STATUS_COLOR: Record<string, string> = {
  success: 'green', failed: 'red', running: 'blue', pending: 'orange', killed: 'default'
}

/** 表格与筛选都用这一份，避免同一页一处中文一处英文 */
const STATUS_LABEL: Record<string, string> = {
  success: '成功', failed: '失败', running: '运行中', pending: '等待中', killed: '已终止',
}

const STATUS_OPTIONS = Object.entries(STATUS_LABEL).map(([value, label]) => ({ value, label }))

function statusTag(s: string) {
  return <Tag color={STATUS_COLOR[s] || 'default'}>{STATUS_LABEL[s] || s}</Tag>
}

/** 周期运维、补数、重跑、手动试跑混在一张表里看不清，按运行类型分开 */
const RUN_TYPE_TABS: { key: RunType; label: string }[] = [
  { key: 'schedule', label: '周期实例' },
  { key: 'backfill', label: '补数据实例' },
  { key: 'rerun', label: '重跑实例' },
  { key: 'manual', label: '手动实例' },
]

type RunType = 'schedule' | 'backfill' | 'rerun' | 'manual'

export default function OperationPage() {
  const { currentWorkspace, user, workspaces, setCurrentWorkspace } = useAppStore()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const platformAdmin = isPlatformAdmin(user)
  const [overview, setOverview] = useState<any>({})
  const [instances, setInstances] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [syncingSchedulerMeta, setSyncingSchedulerMeta] = useState(false)
  /** 与概览「今日实例」一致：仅 created_at 为当日（工作空间时区）的工作流实例 */
  const [todayOnlyWorkflows, setTodayOnlyWorkflows] = useState(false)
  /** 工作流表：仅某个工作流（概览排行下钻） */
  const [workflowFilter, setWorkflowFilter] = useState<number | undefined>()
  /** 工作流表：运行类型标签页，'' 为全部 */
  const [runTypeFilter, setRunTypeFilter] = useState<RunType | ''>('')
  const [runTypeCounts, setRunTypeCounts] = useState<Record<string, number>>({})
  const [diagnosisTarget, setDiagnosisTarget] = useState<DiagnosisTarget | null>(null)
  const [dagTarget, setDagTarget] = useState<InstanceDagTarget | null>(null)
  const [includeAllWorkspaces, setIncludeAllWorkspaces] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const appliedDeepLink = useRef('')
  const { isExpanded, toggle, expandableControl } = useSoftExpandedRows()

  const listParams = {
    include_all_workspaces: includeAllWorkspaces || undefined,
  }

  const loadOverview = async () => {
    if (!wsId) return
    try {
      const ov = await operationApi.overview(wsId, listParams)
      setOverview(ov as any)
      setLoadError('')
    } catch (e: any) {
      setLoadError(describePollError(e))
    }
  }

  const loadInstances = async () => {
    if (!wsId) return
    setListLoading(true)
    try {
      const inst: any = await operationApi.instances(wsId, {
        page,
        page_size: 20,
        status: statusFilter || undefined,
        workflow_id: workflowFilter ?? undefined,
        run_type: runTypeFilter || undefined,
        today_only: todayOnlyWorkflows ? true : undefined,
        ...listParams,
      })
      setRunTypeCounts(inst.run_type_counts || {})
      setInstances(inst.items)
      setTotal(inst.total)
      setLoadError('')
    } catch (e: any) {
      setLoadError(describePollError(e))
    } finally {
      setListLoading(false)
    }
  }

  /** 概览与列表各自上屏：谁先回来谁先渲染，避免被慢的 instances 拖成整页白等 */
  const load = async () => {
    if (!wsId) return
    await Promise.all([loadOverview(), loadInstances()])
  }

  useEffect(() => { load() }, [wsId, statusFilter, page, todayOnlyWorkflows, workflowFilter, runTypeFilter, includeAllWorkspaces])

  useEffect(() => {
    if (!wsId) return undefined
    const timer = window.setInterval(() => { load() }, 15000)
    return () => window.clearInterval(timer)
  }, [wsId, statusFilter, page, todayOnlyWorkflows, workflowFilter, runTypeFilter, includeAllWorkspaces])

  useEffect(() => {
    const inst = Number(searchParams.get('instance') || 0)
    if (!inst) return
    const urlWs = Number(searchParams.get('workspace_id') || 0)
    if (urlWs && Array.isArray(workspaces) && workspaces.length) {
      const hit = workspaces.find((w: any) => Number(w.id) === urlWs)
      if (hit && Number(currentWorkspace?.id) !== urlWs) {
        setCurrentWorkspace(hit)
      }
    }
    const urlWf = Number(searchParams.get('workflow') || 0)
    const key = `${urlWs || ''}:${inst}`
    if (appliedDeepLink.current === key) return
    appliedDeepLink.current = key
    // 告警里点进来是要看「这次运行哪个节点挂了」，直接开运行图，并把列表收窄到同一个工作流当上下文
    setTodayOnlyWorkflows(false)
    setStatusFilter(undefined)
    setWorkflowFilter(urlWf || undefined)
    setPage(1)
    if (urlWf) {
      setDagTarget({ workspaceId: urlWs || wsId!, workflowId: urlWf, instanceId: inst })
    }
  }, [searchParams, workspaces, currentWorkspace, setCurrentWorkspace, wsId])

  const handleWorkflowStop = async (row: any) => {
    const targetWs = row.workspace_id || wsId
    if (!targetWs || !row.workflow_id) return
    await operationApi.stopWorkflowInstance(targetWs, row.workflow_id, row.id)
    message.success('已停止工作流实例')
    load()
  }

  const handleWorkflowRefresh = async (row: any) => {
    const targetWs = row.workspace_id || wsId
    if (!targetWs || !row.workflow_id) return
    await operationApi.refreshWorkflowInstance(targetWs, row.workflow_id, row.id)
    message.success('实例状态已刷新')
    load()
  }

  const handleWorkflowRerun = async (row: any) => {
    const targetWs = row.workspace_id || wsId
    if (!targetWs || !row.workflow_id) return
    await operationApi.rerunWorkflowInstance(targetWs, row.workflow_id, row.id)
    message.success('已提交重跑')
    load()
  }

  const handleRetryFailedNodes = async (row: any) => {
    const targetWs = row.workspace_id || wsId
    if (!targetWs || !row.workflow_id) return
    await operationApi.retryFailedNodes(targetWs, row.workflow_id, row.id)
    message.success('已提交失败节点重试')
    load()
  }

  /** 运行数据由后台持续采集；这里只是手动触发一轮，用于排障 */
  const handleSyncSchedulerMeta = async () => {
    setSyncingSchedulerMeta(true)
    try {
      const syncWs = includeAllWorkspaces && platformAdmin ? undefined : wsId
      const res: any = await schedulerApi.collectRuns(syncWs)
      if (res?.collected === false) {
        message.warning('生产调度未启用，暂无运行数据可采集')
      } else {
        message.success(
          `已采集运行数据：新增实例 ${res?.ingested ?? 0} 条，更新 ${res?.updated_from_ds ?? 0} 条，节点 ${res?.node_rows_touched ?? 0} 条`
        )
      }
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '采集失败，请联系平台管理员检查生产调度连通性')
    } finally {
      setSyncingSchedulerMeta(false)
    }
  }

  /** 排行下钻：只看这个工作流近期的失败实例 */
  const drillToWorkflowFailures = (workflowId: number) => {
    setTodayOnlyWorkflows(false)
    setStatusFilter('failed')
    setWorkflowFilter(workflowId)
    setPage(1)
  }

  const drillFromOverview = (kind: 'today' | 'running' | 'success' | 'failed') => {
    setWorkflowFilter(undefined)
    setPage(1)
    if (kind === 'today') {
      setTodayOnlyWorkflows(true)
      setStatusFilter(undefined)
    } else {
      setTodayOnlyWorkflows(false)
      setStatusFilter(kind)
    }
  }

  /**
   * 概览还没成功加载过时，统计值显示「—」而不是 0。
   * 请求失败时 overview 停在初始的 {}，一排真实的 0 和「没有任何实例」长得一模一样，
   * 之前就是这样让人以为是采集不到数据，其实是请求根本没回来。
   */
  const overviewLoaded = Object.keys(overview || {}).length > 0
  const statValue = (v: unknown) => (overviewLoaded ? (Number(v) || 0) : '—')

  const clickableStat = (inner: ReactNode, onClick: () => void, tip: string) => (
    <Tooltip title={`${tip}（点击下钻）`}>
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
        style={{ cursor: 'pointer', borderRadius: 8, padding: '4px 0' }}
      >
        {inner}
      </div>
    </Tooltip>
  )

  const handleMarkSuccess = (row: any) => {
    let reason = ''
    Modal.confirm({
      title: `将实例 #${row.id} 置为成功`,
      width: 520,
      content: (
        <div>
          <p style={{ marginBottom: 8 }}>
            置成功只改 GIDO 的运行台账，不会让调度引擎重跑这次运行。下游依赖与基线会按「成功」处理，
            后续采集也不再覆盖这条实例的状态。
          </p>
          <Input.TextArea
            rows={3}
            placeholder="处理说明（如：数据已由 XX 手工修复，可放行下游）"
            onChange={(e) => { reason = e.target.value }}
          />
        </div>
      ),
      okText: '确认置成功',
      cancelText: '取消',
      onOk: async () => {
        await workflowApi.overrideInstanceStatus(row.workflow_id, row.id, 'success', reason)
        message.success('已置成功')
        load()
      },
    })
  }

  const handleClearOverride = async (row: any) => {
    await workflowApi.clearInstanceStatusOverride(row.workflow_id, row.id)
    message.success('已撤销人工状态，下次采集恢复以调度引擎为准')
    load()
  }

  const openDiagnosis = (row: any) => setDiagnosisTarget({
    workspaceId: row.workspace_id || wsId!,
    workflowId: row.workflow_id,
    workflowName: row.workflow_name,
    businessDate: row.business_date,
  })

  /**
   * 每行只留一个跟当前状态最相关的动作：失败就重试失败节点，运行中就停止，其余看诊断。
   * 把九个按钮全平铺出来的结果是谁都不知道该点哪个。
   */
  const primaryAction = (row: any): ReactNode => {
    if (row.status === 'running') {
      return (
        <Button type="link" size="small" danger icon={<StopOutlined />} onClick={() => handleWorkflowStop(row)}>
          停止
        </Button>
      )
    }
    if (row.status === 'failed') {
      return (
        <Tooltip title="只重跑失败的那几个节点，比整条重跑省时间">
          <Button type="link" size="small" icon={<ReloadOutlined />} onClick={() => handleRetryFailedNodes(row)}>
            重试
          </Button>
        </Tooltip>
      )
    }
    return (
      <Tooltip title="为什么这次没跑 / 还没跑完">
        <Button type="link" size="small" icon={<QuestionCircleOutlined />} onClick={() => openDiagnosis(row)}>
          诊断
        </Button>
      </Tooltip>
    )
  }

  const moreActions = (row: any) => {
    const items: NonNullable<MenuProps['items']> = []
    // 诊断在非失败/非运行中时已经是主操作，不重复列
    if (row.status === 'failed' || row.status === 'running') {
      items.push({ key: 'diagnose', icon: <QuestionCircleOutlined />, label: '诊断', onClick: () => openDiagnosis(row) })
    }
    if (['failed', 'killed', 'success'].includes(row.status)) {
      items.push({ key: 'rerun', icon: <ReloadOutlined />, label: '整条重跑', onClick: () => handleWorkflowRerun(row) })
    }
    if (['failed', 'killed'].includes(row.status) && !row.status_override) {
      items.push({
        key: 'mark-success',
        icon: <CheckCircleOutlined />,
        label: '置成功（放行下游）',
        onClick: () => handleMarkSuccess(row),
      })
    }
    if (row.status_override) {
      items.push({ key: 'clear-override', label: '撤销人工状态', onClick: () => handleClearOverride(row) })
    }
    items.push({ type: 'divider' })
    items.push({
      key: 'resync',
      label: '重新采集本实例',
      // 后台每 15 秒采集一轮，这里只在怀疑某条没同步上来时用
      onClick: () => handleWorkflowRefresh(row),
    })
    return items
  }

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

  const workflowColumns = [
    ...(includeAllWorkspaces
      ? [{ title: '工作空间', dataIndex: 'workspace_name', width: 110, ellipsis: true }]
      : []),
    {
      title: '工作流',
      dataIndex: 'workflow_name',
      ellipsis: true,
      render: (name: string, row: any) => {
        const failed = Array.isArray(row.failed_nodes) ? row.failed_nodes.filter(Boolean) : []
        const metaBits: ReactNode[] = [
          row.business_date ? `业务日 ${row.business_date}` : '业务日 —',
        ]
        if (failed.length) {
          metaBits.push(
            <Tooltip key="failed" title={failed.join('、')}>
              <span style={{ color: '#cf1322' }}>失败 · {failed[0]}{failed.length > 1 ? ` 等${failed.length}个` : ''}</span>
            </Tooltip>,
          )
        } else if ((row.running_node_count ?? 0) > 0) {
          metaBits.push(`运行中 ${row.running_node_count}`)
        }
        return (
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {name || '—'}
            </div>
            <div style={{ color: '#8c8c8c', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {metaBits.map((bit, i) => (
                  <span key={i}>{i > 0 ? ' · ' : null}{bit}</span>
                ))}
              </span>
              <SoftRowDetailToggle
                expanded={isExpanded(row.id)}
                onToggle={() => toggle(row.id)}
              />
            </div>
          </div>
        )
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 96,
      render: (s: string, row: any) => (
        <Space size={4}>
          {statusTag(s)}
          {row.status_override && (
            <Tooltip
              title={[
                '状态由人工设定，不再跟随调度引擎',
                row.override_reason && `说明：${row.override_reason}`,
                row.override_at && `时间：${formatInTimeZone(row.override_at, displayTz)}`,
              ].filter(Boolean).join('\n')}
            >
              <Tag color="purple">人工</Tag>
            </Tooltip>
          )}
        </Space>
      ),
    },
    {
      title: '时间',
      width: 168,
      render: (_: unknown, row: any) => {
        const fullStart = row.started_at ? formatInTimeZone(row.started_at, displayTz) : ''
        const fullEnd = row.finished_at ? formatInTimeZone(row.finished_at, displayTz) : ''
        return (
          <Tooltip
            title={[
              fullStart && `开始 ${fullStart}`,
              fullEnd && `结束 ${fullEnd}`,
              row.duration_seconds != null && `耗时 ${formatDuration(row.duration_seconds)}`,
            ].filter(Boolean).join('\n') || undefined}
          >
            <div style={{ fontSize: 12.5, lineHeight: 1.4, fontVariantNumeric: 'tabular-nums' }}>
              <div>{row.started_at ? formatInTimeZoneCompact(row.started_at, displayTz) : '—'}</div>
              <div style={{ color: '#8c8c8c' }}>
                {row.finished_at
                  ? `${formatInTimeZoneCompact(row.finished_at, displayTz)} · ${formatDuration(row.duration_seconds)}`
                  : row.status === 'running'
                    ? '运行中'
                    : '—'}
              </div>
            </div>
          </Tooltip>
        )
      },
    },
    {
      title: '操作',
      width: 168,
      fixed: 'right' as const,
      render: (_: unknown, row: any) => (
        <Space size={0} wrap>
          <Button type="link" size="small" icon={<PartitionOutlined />} onClick={() => setDagTarget({
            workspaceId: row.workspace_id || wsId!,
            workflowId: row.workflow_id,
            instanceId: row.id,
          })}>
            运行图
          </Button>
          {primaryAction(row)}
          <Dropdown trigger={['click']} menu={{ items: moreActions(row) }}>
            <Button type="link" size="small">
              更多 <DownOutlined />
            </Button>
          </Dropdown>
        </Space>
      ),
    },
  ]

  const renderInstanceDetail = (row: any) => {
    const failed = Array.isArray(row.failed_nodes) ? row.failed_nodes.filter(Boolean) : []
    const current = Array.isArray(row.current_nodes) ? row.current_nodes.filter(Boolean) : []
    return (
      <div className="gido-soft-expanded-panel">
        <Descriptions size="small" column={2} colon={false}>
          <Descriptions.Item label="实例 ID">{row.id}</Descriptions.Item>
          <Descriptions.Item label="调度运行编号">{row.scheduler_instance_id || '—'}</Descriptions.Item>
          <Descriptions.Item label="触发来源">{row.trigger_label || row.trigger_type || '—'}</Descriptions.Item>
          <Descriptions.Item label="运行类型">{row.run_type || '—'}</Descriptions.Item>
          <Descriptions.Item label="节点进度">
            共 {row.node_total ?? 0}
            {(row.running_node_count ?? 0) > 0 ? ` · 运行 ${row.running_node_count}` : ''}
            {(row.failed_node_count ?? 0) > 0 ? ` · 失败 ${row.failed_node_count}` : ''}
          </Descriptions.Item>
          <Descriptions.Item label="最近同步">
            {row.last_synced_at ? formatInTimeZone(row.last_synced_at, displayTz) : '—'}
          </Descriptions.Item>
          {failed.length ? (
            <Descriptions.Item label="失败节点" span={2}>
              <span style={{ color: '#cf1322' }}>{failed.join('、')}</span>
            </Descriptions.Item>
          ) : null}
          {!failed.length && current.length ? (
            <Descriptions.Item label="当前节点" span={2}>
              {current.join('、')}
            </Descriptions.Item>
          ) : null}
          {row.scheduler_error ? (
            <Descriptions.Item label="引擎错误" span={2}>
              <span style={{ color: '#cf1322' }}>{row.scheduler_error}</span>
            </Descriptions.Item>
          ) : null}
          {row.parent_instance_id ? (
            <Descriptions.Item label="重跑自">#{row.parent_instance_id}</Descriptions.Item>
          ) : null}
        </Descriptions>
      </div>
    )
  }

  const tableTitle = `工作流实例${todayOnlyWorkflows ? ' · 今日' : ''}${
    statusFilter ? ` · ${STATUS_LABEL[statusFilter] || statusFilter}` : ''
  }${workflowFilter ? ` · 工作流筛选中（点上方统计可取消）` : ''}`

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
            调度、补数与运维重跑请在本页查看。数据开发试跑与数据探查请到{' '}
            <Link to={R.batch.runHistory}>运行历史</Link>。
          </span>
        }
      />
      {loadError ? (
        <Alert type="error" showIcon closable style={{ marginBottom: 12 }} message={loadError} />
      ) : null}
      <RunCollectorStatus collector={overview.collector} />
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={4}>
          {clickableStat(
            <Statistic title="今日实例" value={statValue(overview.today_instances)} />,
            () => drillFromOverview('today'),
            `今天新建的工作流实例（按空间时区 ${displayTz}）`
          )}
        </Col>
        <Col span={4}>
          {clickableStat(
            <Statistic title="运行中" value={statValue(overview.running)} valueStyle={{ color: '#1677ff' }} />,
            () => drillFromOverview('running'),
            '状态为 running 的工作流实例'
          )}
        </Col>
        <Col span={4}>
          {clickableStat(
            <Statistic title="成功" value={statValue(overview.success)} valueStyle={{ color: '#52c41a' }} />,
            () => drillFromOverview('success'),
            '状态为 success 的工作流实例'
          )}
        </Col>
        <Col span={4}>
          {clickableStat(
            <Statistic title="失败" value={statValue(overview.failed)} valueStyle={{ color: '#ff4d4f' }} />,
            () => drillFromOverview('failed'),
            '状态为 failed 的工作流实例'
          )}
        </Col>
        <Col span={4}>
          <Statistic title="成功率" value={overviewLoaded ? (overview.success_rate || 'N/A') : '—'} />
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

      {/* 趋势与排行是复盘用的，默认收起，让实例列表留在首屏 */}
      <Collapse
        ghost
        style={{ marginBottom: 12 }}
        items={[{
          key: 'analytics',
          label: '近 7 日趋势与排行',
          children: (
            <>
              <OpsDashboardCharts
                dailyTrend={overview.daily_trend}
                statusDistribution={overview.status_distribution}
              />
              <Row gutter={16}>
                <Col span={12}>
                  <Card size="small" title="出错排行">
                    <Table
                      rowKey="workflow_id"
                      size="small"
                      pagination={false}
                      dataSource={overview.error_ranking || []}
                      locale={{ emptyText: '近 7 日没有失败运行' }}
                      columns={[
                        { title: '工作流', dataIndex: 'workflow_name', ellipsis: true },
                        {
                          title: '失败次数',
                          dataIndex: 'failed_count',
                          width: 100,
                          render: (v: number, row: any) => (
                            <a onClick={() => drillToWorkflowFailures(row.workflow_id)}>{v}</a>
                          ),
                        },
                      ]}
                    />
                  </Card>
                </Col>
                <Col span={12}>
                  <Card size="small" title="耗时排行">
                    <Table
                      rowKey="workflow_id"
                      size="small"
                      pagination={false}
                      dataSource={overview.duration_ranking || []}
                      locale={{ emptyText: '近 7 日没有已结束的运行' }}
                      columns={[
                        { title: '工作流', dataIndex: 'workflow_name', ellipsis: true },
                        { title: '平均耗时', dataIndex: 'avg_seconds', width: 100, render: (v: number) => formatDuration(v) },
                        { title: '最长', dataIndex: 'max_seconds', width: 90, render: (v: number) => formatDuration(v) },
                      ]}
                    />
                  </Card>
                </Col>
              </Row>
            </>
          ),
        }]}
      />

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Select
          placeholder="状态筛选"
          allowClear
          style={{ width: 160 }}
          value={statusFilter}
          onChange={v => { setStatusFilter(v); setPage(1) }}
          options={STATUS_OPTIONS}
        />
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        {/* 后台每 15 秒自动采集，手动触发只是排障工具，不放给普通用户 */}
        {platformAdmin && (
          <Tooltip title="运行数据由后台每 15 秒自动采集；仅在排查采集异常时需要手动触发">
            <Button loading={syncingSchedulerMeta} onClick={handleSyncSchedulerMeta}>立即采集</Button>
          </Tooltip>
        )}
        {platformAdmin && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Switch
              checked={includeAllWorkspaces}
              onChange={(v) => {
                setIncludeAllWorkspaces(v)
                setPage(1)
                // 跨空间看时单工作流的筛选没意义，顺手清掉
                setWorkflowFilter(undefined)
              }}
            />
            <span style={{ color: '#666' }}>全部工作空间</span>
          </span>
        )}
      </div>

      <Tabs
        activeKey={runTypeFilter || 'all'}
        onChange={(k) => { setRunTypeFilter(k === 'all' ? '' : (k as RunType)); setPage(1) }}
        items={[
          { key: 'all', label: '全部' },
          ...RUN_TYPE_TABS.map(t => ({
            key: t.key,
            label: `${t.label}${runTypeCounts[t.key] != null ? ` (${runTypeCounts[t.key]})` : ''}`,
          })),
        ]}
      />

      <div style={{ marginBottom: 8, color: '#666', fontSize: 13 }}>{tableTitle}</div>
      <Table
        loading={listLoading}
        dataSource={instances}
        columns={workflowColumns}
        rowKey="id"
        scroll={{ x: 760 }}
        tableLayout="fixed"
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
        expandable={{
          ...expandableControl,
          expandedRowRender: renderInstanceDetail,
          rowExpandable: () => true,
        }}
      />

      <RunDiagnosisDrawer target={diagnosisTarget} onClose={() => setDiagnosisTarget(null)} />

      <InstanceDagDrawer
        target={dagTarget}
        displayTz={displayTz}
        onClose={() => setDagTarget(null)}
        onChanged={load}
      />

    </div>
  )
}

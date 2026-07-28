/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  Table, Button, Space, Tag, message, Popconfirm, Typography, Alert, Drawer, Tooltip, Input, Select, Card,
  Row, Col, Statistic, Tabs, Descriptions,
} from 'antd'
import {
  ReloadOutlined, LinkOutlined, BugOutlined, StopOutlined, SearchOutlined,
  ClusterOutlined, CloseCircleOutlined, ContainerOutlined, SyncOutlined, ThunderboltOutlined,
  CloudServerOutlined,
} from '@ant-design/icons'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { R } from '../routes'
import { Link } from 'react-router-dom'
import { formatInTimeZone } from '../utils/datetime'
import { openFlinkConsoleUrl } from '../utils/flinkConsole'

const { Paragraph, Text } = Typography

type DeploymentRow = {
  name?: string
  namespace?: string
  workspace_id?: string
  job_id?: string
  job_name?: string
  job_status?: string
  job_type?: string
  lifecycle?: string
  health?: string
  flink_job_id?: string
  error?: string
  spec_state?: string
  image?: string
  flink_version?: string
  job_manager_status?: Record<string, unknown>
  task_manager_status?: Record<string, unknown>
  created_at?: string
}

const HEALTH_COLOR: Record<string, string> = {
  healthy: 'success',
  failed: 'error',
  suspended: 'warning',
  starting: 'processing',
  unknown: 'default',
}

const HEALTH_LABEL: Record<string, string> = {
  healthy: '运行中',
  failed: '失败',
  suspended: '已暂停',
  starting: '启动中',
  unknown: '未知',
}

const PLATFORM_STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  running: '运行中',
  finished: '已完成',
  failed: '失败',
  cancelled: '已停止',
}

const FLINK_STATUS_LABEL: Record<string, string> = {
  APPLICATION_PENDING_JOB_ID: '等待 JobId',
  STARTING: '启动中',
  DEPLOYING: '部署中',
  CREATED: '已创建',
  STABLE: '稳定',
  SUSPENDED: '已暂停',
  NOT_FOUND_ON_JM: 'JM 未找到',
  UNKNOWN: '未知',
  RUNNING: '运行中',
  INITIALIZING: '初始化',
  FINISHED: '已完成',
  FAILED: '失败',
  CANCELED: '已取消',
  CANCELLED: '已取消',
  CANCELLING: '取消中',
}

function flinkStatusDisplay(fs: string | undefined) {
  if (!fs) return <Text type="secondary">—</Text>
  const color: Record<string, string> = {
    APPLICATION_PENDING_JOB_ID: 'orange',
    STARTING: 'processing',
    DEPLOYING: 'processing',
    CREATED: 'processing',
    STABLE: 'processing',
    SUSPENDED: 'warning',
    NOT_FOUND_ON_JM: 'volcano',
    UNKNOWN: 'default',
    RUNNING: 'processing',
    INITIALIZING: 'processing',
    FINISHED: 'success',
    FAILED: 'error',
    CANCELED: 'warning',
    CANCELLED: 'warning',
    CANCELLING: 'warning',
  }
  return <Tag color={color[fs] || 'blue'}>{FLINK_STATUS_LABEL[fs] || fs}</Tag>
}

function isOperatorJob(j: any) {
  if (j.job_type === 'JAR') return (j.flink_jar_submit_mode || 'flink_operator') === 'flink_operator'
  if (j.job_type === 'SQL') return (j.flink_sql_submit_mode || 'flink_operator') === 'flink_operator'
  return false
}

/** Operator：有 deployment 就对账（含库内已停止，用于发现僵尸集群）。Session：有 jobId 才轮询。 */
function jobNeedsFlinkStatusPoll(j: any) {
  const st = (j.status || '').toString().toLowerCase()
  if (isOperatorJob(j) && j.flink_operator_deployment_name) return true
  if (st === 'cancelled' || st === 'finished' || st === 'failed') return false
  if (j.flink_job_id) return true
  const mode = (j.flink_sql_submit_mode || 'session').toString().toLowerCase()
  if (mode === 'kubernetes_application') return Boolean(j.flink_application_cluster_id)
  return false
}

function diagnosticsButtonLabel(row: any) {
  if (row.last_submit_error) return '启动失败'
  if (row.flink_job_id) return '运行时异常'
  return '诊断'
}

export default function StreamMonitorPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [jobs, setJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [flinkMap, setFlinkMap] = useState<Record<number, { flink_status?: string; status?: string }>>({})
  const [diagOpen, setDiagOpen] = useState(false)
  const [diagRow, setDiagRow] = useState<any | null>(null)
  const [diagExceptions, setDiagExceptions] = useState<any>(null)
  const [diagSync, setDiagSync] = useState<any>(null)
  const [overview, setOverview] = useState<Record<string, any> | null>(null)
  const [overviewErr, setOverviewErr] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState<string | undefined>()
  const [deployFilter, setDeployFilter] = useState<string | undefined>()
  const [stateFilter, setStateFilter] = useState<string | undefined>()

  const jobsRef = useRef<any[]>([])

  useEffect(() => {
    jobsRef.current = jobs
  }, [jobs])

  const loadJobs = useCallback(async (showSpinner = true) => {
    if (!wsId) return
    if (showSpinner) setLoading(true)
    try {
      const [list, ov]: any = await Promise.all([
        streamingApi.listJobs(wsId),
        streamingApi.operatorOverview(wsId).catch((e: any) => {
          setOverviewErr(e?.response?.data?.detail || e.message || '加载 FlinkDeployment 概览失败')
          return null
        }),
      ])
      setJobs(list)
      if (ov) {
        setOverview(ov)
        setOverviewErr(null)
      }
    } finally {
      if (showSpinner) setLoading(false)
    }
  }, [wsId])

  const syncAll = useCallback(async () => {
    if (!wsId || jobs.length === 0) return
    try {
      const res: any = await streamingApi.syncJobsStatus(wsId)
      const next: Record<number, { flink_status?: string; status?: string }> = {}
      for (const s of res?.items || []) {
        next[s.id] = { flink_status: s.flink_status, status: s.status }
      }
      setFlinkMap(prev => ({ ...prev, ...next }))
      message.success(`已同步 ${res?.synced ?? 0} 个活跃作业`)
      await loadJobs()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e.message || '同步失败')
    }
  }, [wsId, jobs.length, loadJobs])

  useEffect(() => { loadJobs() }, [loadJobs])

  const openDiagnostics = async (row: any) => {
    setDiagRow(row)
    setDiagExceptions(null)
    setDiagSync(null)
    setDiagOpen(true)
    try {
      const s: any = await streamingApi.getStatus(row.id)
      setDiagSync(s)
    } catch (e: any) {
      setDiagSync({ error: e?.response?.data?.detail || e.message })
    }
    if (!row.flink_job_id) return
    try {
      const ex: any = await streamingApi.getExceptions(row.id)
      setDiagExceptions(ex)
    } catch (e: any) {
      setDiagExceptions({ error: e?.response?.data?.detail || e.message })
    }
  }

  const canRun = can(user, P.GIDO_STREAM_RUN, currentWorkspace)

  const handleStop = async (row: any) => {
    try {
      await streamingApi.cancelJob(row.id)
      message.success('已请求停止')
      await loadJobs()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '停止失败')
    }
  }

  /** 轮询 Flink 回填库内 status；主表只展示统一作业状态，底层状态留在 tooltip / 诊断中。 */
  useEffect(() => {
    let alive = true
    const poll = async () => {
      if (!wsId || !alive) return
      const list = jobsRef.current
      if (!list.some(jobNeedsFlinkStatusPoll)) return
      try {
        const res: any = await streamingApi.syncJobsStatus(wsId)
        if (!alive) return
        const nextMap: Record<number, { flink_status?: string; status?: string }> = {}
        for (const s of res?.items || []) {
          nextMap[s.id] = { flink_status: s.flink_status, status: s.status }
        }
        setFlinkMap(prev => ({ ...prev, ...nextMap }))
        // 有状态变化时轻量刷新列表；避免每轮再打一遍 listJobs 加重负载
        if ((res?.synced || 0) > 0) {
          setJobs(prev => prev.map(j => {
            const u = nextMap[j.id]
            return u?.status != null ? { ...j, status: u.status } : j
          }))
        }
      } catch { /* ignore */ }
    }
    poll()
    const t = window.setInterval(poll, 8000)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [wsId])

  const unifiedJobState = (row: any) => {
    const platform = String(flinkMap[row.id]?.status || row.status || '').toLowerCase()
    const flink = String(flinkMap[row.id]?.flink_status || row.flink_status || '')
    if (/NOT_FOUND_ON_OPERATOR|CLEANED_UP|SUSPENDED/i.test(flink) || platform === 'cancelled') {
      return { key: 'stopped', label: '已停止', color: 'warning' }
    }
    if (row.last_submit_error || platform === 'failed' || /FAILED/i.test(flink)) {
      return { key: 'needs_attention', label: '需处理', color: 'error' }
    }
    if (platform === 'draft') return { key: 'draft', label: '草稿', color: 'default' }
    if (/DEPLOY|START|INITIALIZING|CREATED|PENDING/i.test(flink)) {
      return { key: 'active', label: '启动中', color: 'processing' }
    }
    if (platform === 'running' || /RUNNING|STABLE/i.test(flink)) {
      return { key: 'active', label: '运行中', color: 'processing' }
    }
    if (platform === 'finished' || /FINISHED/i.test(flink)) {
      return { key: 'terminal', label: '已结束', color: 'success' }
    }
    if (/CANCEL|NOT_FOUND_ON_JM/i.test(flink)) {
      return { key: 'stopped', label: '已停止', color: 'warning' }
    }
    return { key: 'other', label: '未知', color: 'default' }
  }

  const filteredJobs = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return jobs.filter(row => {
      const stateClass = unifiedJobState(row).key
      const deployMode = isOperatorJob(row) ? 'operator' : ((row.flink_sql_submit_mode || row.flink_jar_submit_mode || 'session').toString().toLowerCase())
      if (typeFilter && row.job_type !== typeFilter) return false
      if (deployFilter && deployMode !== deployFilter) return false
      if (stateFilter) {
        if (stateClass !== stateFilter) return false
      }
      if (!kw) return true
      const hay = [
        row.name,
        row.id,
        row.flink_operator_deployment_name,
        row.flink_application_cluster_id,
        row.flink_job_id,
        row.last_submitted_by_username,
      ].filter(Boolean).join(' ').toLowerCase()
      return hay.includes(kw)
    })
  }, [jobs, flinkMap, keyword, typeFilter, deployFilter, stateFilter])

  const renderUnifiedState = (row: any) => {
    const platform = flinkMap[row.id]?.status || row.status
    const flink = flinkMap[row.id]?.flink_status
    const state = unifiedJobState(row)
    return (
      <Tooltip title={`平台记录：${PLATFORM_STATUS_LABEL[platform] || platform || '—'}；Flink 原始状态：${flink ? (FLINK_STATUS_LABEL[flink] || flink) : '—'}`}>
        <Tag color={state.color}>{state.label}</Tag>
      </Tooltip>
    )
  }

  const runtime = overview?.runtime || {}
  const summary = overview?.summary || {}
  const deployments: DeploymentRow[] = overview?.deployments || []

  const deploymentColumns = [
    {
      title: '作业',
      dataIndex: 'name',
      ellipsis: true,
      render: (name: string, row: DeploymentRow) => (
        <div>
          <Space size={6} wrap>
            <Text strong>{row.job_name || (row.job_id ? `作业 #${row.job_id}` : name)}</Text>
            {row.job_status && <Tag>{row.job_status}</Tag>}
          </Space>
          <div style={{ marginTop: 2 }}>
            <Tooltip title="FlinkDeployment CR 名称">
              <Text code type="secondary" style={{ fontSize: 11 }}>{name}</Text>
            </Tooltip>
          </div>
        </div>
      ),
    },
    {
      title: '类型',
      dataIndex: 'job_type',
      width: 64,
      render: (t: string) => t ? <Tag>{t.toUpperCase()}</Tag> : '—',
    },
    {
      title: '健康',
      dataIndex: 'health',
      width: 88,
      render: (h: string) => <Tag color={HEALTH_COLOR[h] || 'default'}>{HEALTH_LABEL[h] || h || '—'}</Tag>,
    },
    {
      title: 'Lifecycle',
      dataIndex: 'lifecycle',
      width: 120,
      render: (lc: string) => lc ? <Tag>{lc}</Tag> : '—',
    },
    {
      title: 'Flink JobId',
      dataIndex: 'flink_job_id',
      width: 120,
      ellipsis: true,
      render: (id: string) => id ? <Text code style={{ fontSize: 11 }}>{id.slice(0, 8)}…</Text> : '—',
    },
    {
      title: 'JM / TM',
      key: 'pods',
      width: 140,
      render: (_: unknown, row: DeploymentRow) => {
        const jm = row.job_manager_status
        const tm = row.task_manager_status
        const jmOk = jm && /ready|running|stable/i.test(JSON.stringify(jm))
        const tmOk = tm && /ready|running|stable/i.test(JSON.stringify(tm))
        return (
          <Space size={4}>
            <Tooltip title={`JobManager: ${jm ? JSON.stringify(jm) : '无'}`}>
              <Tag color={jmOk ? 'success' : 'default'}>JM</Tag>
            </Tooltip>
            <Tooltip title={`TaskManager: ${tm ? JSON.stringify(tm) : '无'}`}>
              <Tag color={tmOk ? 'success' : 'default'}>TM</Tag>
            </Tooltip>
          </Space>
        )
      },
    },
    {
      title: '错误',
      dataIndex: 'error',
      ellipsis: true,
      render: (e: string) => e ? <Text type="danger" style={{ fontSize: 12 }}>{e}</Text> : '—',
    },
  ]

  const columns = [
    { title: '作业名', dataIndex: 'name', key: 'name', width: 180, ellipsis: true },
    { title: '类型', dataIndex: 'job_type', key: 'job_type', width: 64, render: (t: string) => <Tag>{t}</Tag> },
    {
      title: '部署',
      key: 'deploy',
      width: 88,
      render: (_: unknown, row: any) => {
        if (row.job_type !== 'SQL' && row.job_type !== 'JAR') return <Text type="secondary">—</Text>
        const legacy = (row.job_type === 'JAR' && row.flink_jar_submit_mode !== 'flink_operator')
          || (row.job_type === 'SQL' && (row.flink_sql_submit_mode || 'flink_operator') !== 'flink_operator')
        if (legacy) {
          const m = row.job_type === 'SQL'
            ? (row.flink_sql_submit_mode || '').toString().toLowerCase()
            : 'session'
          const label = m === 'kubernetes_application' ? 'App' : 'Session'
          return <Tag color="geekblue">{label}</Tag>
        }
        return <Tag color="purple">Operator</Tag>
      },
    },
    {
      title: '作业状态',
      key: 'state',
      width: 128,
      render: (_: any, row: any) => renderUnifiedState(row),
    },
    {
      title: '部署标识',
      key: 'cid',
      width: 140,
      ellipsis: true,
      render: (_: unknown, row: any) => {
        const dep = row.flink_operator_deployment_name
        const cid = row.flink_application_cluster_id
        const label = dep || cid
        if (!label) return <Text type="secondary">—</Text>
        return (
          <Tooltip title={dep ? 'FlinkDeployment' : 'K8s Application clusterID'}>
            <Typography.Paragraph copyable={{ text: label }} style={{ marginBottom: 0, fontSize: 12 }}>
              {label}
            </Typography.Paragraph>
          </Tooltip>
        )
      },
    },
    {
      title: '最近提交',
      key: 'lsub',
      width: 132,
      render: (_: unknown, row: any) => (
        <div style={{ fontSize: 12, lineHeight: 1.35 }}>
          <div>{formatInTimeZone(row.last_submitted_at, displayTz)}</div>
          <Text type="secondary">{row.last_submitted_by_username || '—'}</Text>
        </div>
      ),
    },
    {
      title: 'K8s Flink UI',
      key: 'fc',
      width: 128,
      render: (_: any, row: any) => {
        const isOp = row.flink_console_mode === 'operator' || (
          row.job_type === 'JAR' && row.flink_jar_submit_mode === 'flink_operator'
        )
        if (!row.flink_console_url) {
          return isOp ? (
            <Tooltip title={row.flink_k8s_jm_service || '等待解析 JM REST / NodePort'}>
              <Text type="secondary">待就绪</Text>
            </Tooltip>
          ) : (
            <Text type="secondary">—</Text>
          )
        }
        const tip = isOp
          ? `FlinkDeployment · Service ${row.flink_operator_deployment_name || ''}-rest`
          : undefined
        return (
          <Tooltip title={tip}>
            <Button
              type="link"
              size="small"
              icon={<LinkOutlined />}
              style={{ padding: 0, height: 'auto' }}
              onClick={() => openFlinkConsoleUrl(row.flink_console_url, row.id)}
            >
              {isOp ? 'K8s 作业 UI' : '打开'}
            </Button>
          </Tooltip>
        )
      },
    },
    {
      title: '启动失败 / 诊断',
      key: 'diag',
      width: 118,
      render: (_: any, row: any) => (
        <Button
          type="link"
          size="small"
          icon={<BugOutlined />}
          onClick={() => openDiagnostics(row)}
        >
          {diagnosticsButtonLabel(row)}
        </Button>
      ),
    },
    { title: 'Flink Job ID', dataIndex: 'flink_job_id', key: 'flink_job_id', ellipsis: true, width: 140 },
    { title: '并行度', dataIndex: 'parallelism', key: 'parallelism', width: 64 },
    ...(canRun
      ? [{
          title: '停止',
          key: 'stop',
          width: 76,
          render: (_: unknown, row: any) => {
            const opJar =
              row.job_type === 'JAR'
              && (row.flink_jar_submit_mode || '').toString() === 'flink_operator'
            if (row.flink_job_id || opJar) {
              return (
                <Popconfirm
                  title={opJar ? '删除 FlinkDeployment 并回收 JM/TM Pod？' : '在 Flink 上停止该作业？'}
                  onConfirm={() => handleStop(row)}
                >
                  <Button type="link" size="small" danger icon={<StopOutlined />} />
                </Popconfirm>
              )
            }
            const cid = row.flink_application_cluster_id
            const isApp = (row.flink_sql_submit_mode || '').toString().toLowerCase() === 'kubernetes_application'
            if (isApp && cid) {
              return (
                <Tooltip title="尚无 Job ID 时无法在平台侧调用 JM 停止接口；请配置 FLINK_K8S_APPLICATION_JM_REST_TEMPLATE 或在 Flink/K8s 控制台停止该 Application 集群。">
                  <Text type="secondary">—</Text>
                </Tooltip>
              )
            }
            return <Text type="secondary">—</Text>
          },
        }]
      : []),
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <Typography.Title level={4} style={{ marginBottom: 4 }}>作业运维</Typography.Title>
          <Paragraph type="secondary" style={{ marginBottom: 0, maxWidth: 920 }}>
            对标实时计算「运维管理」：查看作业与 FlinkDeployment 运行态、停止、诊断与 Flink UI。
            本页周期性同步集群状态；逻辑编辑与部署上线请到
            {' '}<Link to={R.stream.studio}>作业开发</Link>
            ，依赖包请到
            {' '}<Link to={R.stream.resources}>资源管理</Link>。
          </Paragraph>
        </div>
      </div>

      {overviewErr && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="FlinkDeployment 概览加载失败" description={overviewErr} />
      )}

      <Row gutter={[12, 12]} style={{ marginBottom: 12 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="FlinkDeployment" value={summary.deployments_total ?? 0} prefix={<ClusterOutlined />} suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {summary.running ?? 0} 运行</Text>} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="失败 / 暂停" value={summary.failed ?? 0} valueStyle={{ color: (summary.failed ?? 0) > 0 ? '#ff4d4f' : undefined }} prefix={<CloseCircleOutlined />} suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {summary.suspended ?? 0} 暂停</Text>} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="GIDO 作业" value={summary.jobs_total ?? jobs.length} prefix={<ContainerOutlined />} suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {summary.jobs_running ?? 0} 运行</Text>} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card size="small">
            <Statistic title="启动中" value={summary.starting ?? 0} prefix={<SyncOutlined spin={(summary.starting ?? 0) > 0} />} />
          </Card>
        </Col>
      </Row>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={{ xs: 1, md: 2 }} size="small">
          <Descriptions.Item label="K8s 命名空间"><Text code>{overview?.namespace || runtime.operator_namespace || '—'}</Text></Descriptions.Item>
          <Descriptions.Item label="Flink 版本">{runtime.flink_version || '—'}</Descriptions.Item>
          <Descriptions.Item label="运行时镜像" span={2}>
            <Text code style={{ wordBreak: 'break-all' }}>{runtime.runtime_image || '—'}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="Paimon Warehouse" span={2}>
            <Text code style={{ wordBreak: 'break-all' }}>{runtime.paimon_warehouse_default || '—'}</Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card size="small" style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18, padding: '4px 0', flexWrap: 'wrap' }}>
          <div style={{ textAlign: 'center', minWidth: 120 }}>
            <div style={{ fontSize: 22, color: '#722ed1' }}><ContainerOutlined /></div>
            <Text strong>作业开发</Text>
          </div>
          <Text type="secondary">→</Text>
          <div style={{ textAlign: 'center', minWidth: 120 }}>
            <div style={{ fontSize: 22, color: '#1677ff' }}><ClusterOutlined /></div>
            <Text strong>FlinkDeployment</Text>
          </div>
          <Text type="secondary">→</Text>
          <div style={{ textAlign: 'center', minWidth: 120 }}>
            <div style={{ fontSize: 22, color: '#13c2c2' }}><CloudServerOutlined /></div>
            <Text strong>JM / TM Pod</Text>
          </div>
          <Text type="secondary">→</Text>
          <div style={{ textAlign: 'center', minWidth: 120 }}>
            <div style={{ fontSize: 22, color: '#52c41a' }}><ThunderboltOutlined /></div>
            <Text strong>Flink Job</Text>
          </div>
        </div>
      </Card>
      <Tabs
        tabBarStyle={{ position: 'sticky', top: 0, zIndex: 10, background: '#fff', paddingTop: 8 }}
        tabBarExtraContent={(
          <Space wrap>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => loadJobs(true)} loading={loading}>刷新列表</Button>
            <Button size="small" type="primary" onClick={syncAll}>全量同步状态</Button>
          </Space>
        )}
        items={[
          {
            key: 'jobs',
            label: '作业视图',
            children: (
              <>
                <Card size="small" style={{ marginBottom: 12 }}>
                  <Space wrap>
                    <Input
                      allowClear
                      prefix={<SearchOutlined />}
                      placeholder="搜索作业名 / CR / JobId / 提交人"
                      value={keyword}
                      onChange={e => setKeyword(e.target.value)}
                      style={{ width: 280 }}
                    />
                    <Select allowClear placeholder="类型" value={typeFilter} onChange={setTypeFilter} style={{ width: 120 }} options={[{ value: 'SQL', label: 'SQL' }, { value: 'JAR', label: 'JAR' }]} />
                    <Select allowClear placeholder="部署模式" value={deployFilter} onChange={setDeployFilter} style={{ width: 150 }} options={[{ value: 'operator', label: 'Operator' }, { value: 'kubernetes_application', label: 'K8s Application' }, { value: 'session', label: 'Session' }]} />
                    <Select
                      allowClear
                      placeholder="运行状态"
                      value={stateFilter}
                      onChange={setStateFilter}
                      style={{ width: 150 }}
                      options={[
                        { value: 'active', label: '运行中' },
                        { value: 'terminal', label: '已结束' },
                        { value: 'stopped', label: '已停止' },
                        { value: 'draft', label: '草稿' },
                        { value: 'needs_attention', label: '需处理' },
                      ]}
                    />
                    <Text type="secondary">共 {filteredJobs.length} / {jobs.length} 个作业</Text>
                  </Space>
                </Card>
                <Table rowKey="id" loading={loading} dataSource={filteredJobs} columns={columns as any} scroll={{ x: 1320 }} pagination={{ pageSize: 12 }} />
              </>
            ),
          },
          {
            key: 'deployments',
            label: 'FlinkDeployment 视图',
            children: (
              <Table
                size="small"
                rowKey="name"
                loading={loading}
                dataSource={deployments}
                columns={deploymentColumns as any}
                pagination={{ pageSize: 10, showSizeChanger: true }}
                locale={{ emptyText: '当前工作空间暂无 FlinkDeployment（提交作业后将自动创建）' }}
                expandable={{
                  expandedRowRender: (row: DeploymentRow) => (
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}>
                      {JSON.stringify({
                        image: row.image,
                        jobManager: row.job_manager_status,
                        taskManager: row.task_manager_status,
                      }, null, 2)}
                    </pre>
                  ),
                  rowExpandable: (row) => Boolean(row.job_manager_status || row.task_manager_status || row.image),
                }}
              />
            ),
          },
        ]}
      />

      <Drawer
        title={diagRow ? `诊断 · ${diagRow.name}` : '诊断'}
        width={720}
        open={diagOpen}
        onClose={() => { setDiagOpen(false); setDiagRow(null); setDiagExceptions(null); setDiagSync(null) }}
        destroyOnClose
      >
        <Typography.Title level={5} style={{ marginTop: 0 }}>平台侧同步（最近一次拉取）</Typography.Title>
        {diagSync ? (
          <pre style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            background: '#f0f5ff',
            border: '1px solid #d6e4ff',
            borderRadius: 8,
            padding: 12,
            fontSize: 12,
            maxHeight: 220,
            overflow: 'auto',
          }}>
            {JSON.stringify(diagSync, null, 2)}
          </pre>
        ) : (
          <Text type="secondary">加载中…</Text>
        )}

        {diagRow?.flink_operational?.hints?.length ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>就绪度与运维建议</Typography.Title>
            <Alert
              type={diagRow.flink_operational.readiness === 'blocked' ? 'error' : diagRow.flink_operational.readiness === 'warning' ? 'warning' : 'info'}
              showIcon
              message={`就绪度：${diagRow.flink_operational.readiness}`}
              description={(
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13 }}>
                  {diagRow.flink_operational.hints.map((h: string, i: number) => (
                    <li key={i}>{h}</li>
                  ))}
                </ul>
              )}
            />
          </>
        ) : null}

        {diagRow?.last_submit_error ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>最近一次提交到 Flink 失败（启动阶段）</Typography.Title>
            <pre style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              background: '#fff2f0',
              border: '1px solid #ffccc7',
              borderRadius: 8,
              padding: 12,
              fontSize: 12,
              maxHeight: 280,
              overflow: 'auto',
            }}>
              {diagRow.last_submit_error}
            </pre>
          </>
        ) : (
          <Paragraph type="secondary" style={{ marginTop: 16 }}>暂无最近一次提交失败的记录。</Paragraph>
        )}

        {diagRow?.flink_console_url && (
          <p style={{ marginTop: 12 }}>
            <a href={diagRow.flink_console_url} target="_blank" rel="noreferrer">
              <LinkOutlined /> Flink Web UI（作业详情或 JM 总览）
            </a>
          </p>
        )}

        <Typography.Title level={5} style={{ marginTop: 16 }}>Flink JobManager · 运行时异常（REST）</Typography.Title>
        {!diagRow?.flink_job_id ? (
          <Text type="secondary">
            尚无 Flink Job ID，无法拉取 /jobs/&lt;id&gt;/exceptions。
            {diagRow?.flink_application_cluster_id ? ' Application 模式下请先在 Web UI 确认 Job 是否已出现，或检查是否已配置 JM REST 模板以自动回填 Job ID。' : ''}
          </Text>
        ) : (
          <pre style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            background: '#f5f5f5',
            borderRadius: 8,
            padding: 12,
            fontSize: 12,
            maxHeight: 360,
            overflow: 'auto',
          }}>
            {diagExceptions ? JSON.stringify(diagExceptions, null, 2) : '加载中…'}
          </pre>
        )}
      </Drawer>
    </div>
  )
}

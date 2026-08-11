/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  Table, Button, Space, Tag, message, Typography, Alert, Drawer, Tooltip, Input, Select, Card,
  Row, Col, Statistic, Tabs, Descriptions, Modal, Form, InputNumber, Radio, Switch, Dropdown, Checkbox,
} from 'antd'
import {
  ReloadOutlined, LinkOutlined, BugOutlined, StopOutlined, SearchOutlined,
  ClusterOutlined, CloseCircleOutlined, ContainerOutlined, SyncOutlined, ThunderboltOutlined,
  CloudServerOutlined,
  RocketOutlined, HistoryOutlined, RetweetOutlined, MoreOutlined,
} from '@ant-design/icons'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { R } from '../routes'
import { Link } from 'react-router-dom'
import { formatInTimeZone } from '../utils/datetime'
import { openFlinkConsoleUrl } from '../utils/flinkConsole'
import StreamRuntimeConfig, {
  buildStreamRuntimeProperties,
  EMPTY_OPERATOR_RESOURCES,
  parseStreamRuntimeConfig,
  type OperatorResourceForm,
} from '../components/StreamRuntimeConfig'

const { Paragraph, Text } = Typography

/** 恢复点/操作耗时展示：秒 → 可读文案 */
function formatElapsedSeconds(seconds?: number | null): string {
  if (seconds == null || Number.isNaN(Number(seconds))) return '—'
  const s = Math.max(0, Math.floor(Number(seconds)))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rest = s % 60
  if (m < 60) return rest ? `${m}m ${rest}s` : `${m}m`
  const h = Math.floor(m / 60)
  const remM = m % 60
  return remM ? `${h}h ${remM}m` : `${h}h`
}

function restorePointDurationSeconds(row: any): number | null {
  if (row?.duration_seconds != null && !Number.isNaN(Number(row.duration_seconds))) {
    return Math.max(0, Math.floor(Number(row.duration_seconds)))
  }
  const startRaw = row?.created_at
  const endRaw = row?.completed_at
  if (!startRaw || !endRaw) return null
  const start = new Date(startRaw).getTime()
  const end = new Date(endRaw).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
  return Math.max(0, Math.floor((end - start) / 1000))
}

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

const OPERATION_TYPE_LABEL: Record<string, string> = {
  stop: '保存并停止',
  cancel: '清理集群',
  'force-stop': '清理集群',
  force_stop: '清理集群',
  restart: '重启/恢复',
  deploy: '部署',
  'stateless-start': '无状态启动',
}

/** 保存并停止默认等待 Snapshot 时长（秒），与后端 StreamingStopBody 默认一致 */
const STOP_SAVEPOINT_TIMEOUT_SECONDS = 300

const OPERATION_STATUS_LABEL: Record<string, string> = {
  pending: '待执行',
  running: '进行中',
  succeeded: '成功',
  failed: '失败',
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
  JM_UNREACHABLE: 'JM 不可达',
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
    JM_UNREACHABLE: 'error',
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

function asItems(value: any, key?: string): any[] {
  if (Array.isArray(value)) return value
  if (key && Array.isArray(value?.[key])) return value[key]
  if (Array.isArray(value?.items)) return value.items
  return []
}

function parseJsonObject(value: unknown): Record<string, any> {
  if (value && typeof value === 'object') return value as Record<string, any>
  if (typeof value !== 'string' || !value.trim()) return {}
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function releaseStatus(release: any): string {
  return String(release?.status || release?.approval_status || release?.state || '').toLowerCase()
}

function isApprovedNotDeployed(release: any, job?: any): boolean {
  const status = releaseStatus(release)
  const deployment = String(release?.deployment_status || '').toLowerCase()
  return (status === 'approved' || release?.approved === true)
    && (!job?.current_running_release_id || Number(job.current_running_release_id) !== Number(release?.id))
    && !release?.deployed_at
    && !['deployed', 'deploying', 'running'].includes(deployment)
}

export default function StreamMonitorPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [jobs, setJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [flinkMap, setFlinkMap] = useState<Record<number, { flink_status?: string; status?: string; lifecycle_state?: string }>>({})
  const [diagOpen, setDiagOpen] = useState(false)
  const [diagRow, setDiagRow] = useState<any | null>(null)
  const [diagExceptions, setDiagExceptions] = useState<any>(null)
  const [diagSync, setDiagSync] = useState<any>(null)
  const [diagCheckpoints, setDiagCheckpoints] = useState<any>(null)
  const [diagObservability, setDiagObservability] = useState<any>(null)
  const [overview, setOverview] = useState<Record<string, any> | null>(null)
  const [overviewErr, setOverviewErr] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')
  const [typeFilter, setTypeFilter] = useState<string | undefined>()
  const [deployFilter, setDeployFilter] = useState<string | undefined>()
  const [stateFilter, setStateFilter] = useState<string | undefined>()
  const [releaseMap, setReleaseMap] = useState<Record<number, any[]>>({})
  const [actionOpen, setActionOpen] = useState(false)
  const [actionKind, setActionKind] = useState<'deploy' | 'restart'>('deploy')
  const [actionRow, setActionRow] = useState<any | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [releaseId, setReleaseId] = useState<number | string | undefined>()
  const [parallelism, setParallelism] = useState(1)
  const [advancedJson, setAdvancedJson] = useState('{}')
  const [resourceTier, setResourceTier] = useState('')
  const [operatorResources, setOperatorResources] = useState<OperatorResourceForm>({ ...EMPTY_OPERATOR_RESOURCES })
  const [restoreMode, setRestoreMode] = useState<'latest' | 'specific' | 'last-state' | 'stateless'>('latest')
  const [restorePointId, setRestorePointId] = useState<number | string | undefined>()
  const [allowNonRestoredState, setAllowNonRestoredState] = useState(false)
  const [restorePoints, setRestorePoints] = useState<any[]>([])
  const [restoreDrawerOpen, setRestoreDrawerOpen] = useState(false)
  const [operationDrawerOpen, setOperationDrawerOpen] = useState(false)
  const [operationRow, setOperationRow] = useState<any | null>(null)
  const [operations, setOperations] = useState<any[]>([])
  const [drawerLoading, setDrawerLoading] = useState(false)

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
      const releases = await Promise.all((Array.isArray(list) ? list : []).map(async (job: any) => {
        try {
          const value: any = await streamingApi.listReleases(job.id)
          return [job.id, asItems(value, 'releases')] as const
        } catch {
          return [job.id, []] as const
        }
      }))
      setReleaseMap(Object.fromEntries(releases))
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
      const next: Record<number, { flink_status?: string; status?: string; lifecycle_state?: string }> = {}
      for (const s of res?.items || []) {
        next[s.id] = {
          flink_status: s.flink_status,
          status: s.status,
          lifecycle_state: s.lifecycle_state,
        }
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
    setDiagCheckpoints(null)
    setDiagObservability(null)
    setDiagOpen(true)
    try {
      const s: any = await streamingApi.getStatus(row.id)
      setDiagSync(s)
    } catch (e: any) {
      setDiagSync({ error: e?.response?.data?.detail || e.message })
    }
    try {
      const ck: any = await streamingApi.getCheckpoints(row.id)
      setDiagCheckpoints(ck)
    } catch (e: any) {
      setDiagCheckpoints({ available: false, reason: e?.response?.data?.detail || e.message })
    }
    try {
      const ex: any = await streamingApi.getExceptions(row.id)
      setDiagExceptions(ex)
    } catch (e: any) {
      setDiagExceptions({ error: e?.response?.data?.detail || e.message })
    }
    if (row.definition_kind === 'pipeline') {
      try {
        setDiagObservability(await streamingApi.getPipelineObservability(row.id))
      } catch (e: any) {
        setDiagObservability({ error: e?.response?.data?.detail || e.message })
      }
    }
  }

  const canRun = can(user, P.GIDO_STREAM_RUN, currentWorkspace)
  const stoppingJobs = useMemo(
    () => jobs.filter(j => {
      const lc = String(j.lifecycle_state || '').toUpperCase()
      return lc === 'SAVING_STATE' || lc === 'SUSPENDING'
    }),
    [jobs],
  )
  const cleaningJobs = useMemo(
    () => jobs.filter(j => String(j.lifecycle_state || '').toUpperCase() === 'FORCE_STOPPING'),
    [jobs],
  )
  const notifiedStopOpsRef = useRef<Set<number>>(new Set())

  const handleStop = async (row: any) => {
    try {
      const res: any = await streamingApi.stopJob(row.id, {
        mode: 'savepoint',
        timeout_seconds: STOP_SAVEPOINT_TIMEOUT_SECONDS,
      })
      // 不用 duration:0 的全局 loading：会跟到其它页面置顶。进度只留在本页 Alert。
      message.success(res?.message || '已提交「保存并停止」，正在等待 Snapshot，进度见本页提示')
      if (res?.operation_id != null) {
        notifiedStopOpsRef.current.delete(Number(res.operation_id))
      }
      setJobs(prev => prev.map(j => (
        j.id === row.id
          ? {
              ...j,
              lifecycle_state: res?.lifecycle_state || 'SAVING_STATE',
              _pending_stop_operation_id: res?.operation_id,
            }
          : j
      )))
      await loadJobs(false)
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || '保存并停止失败'
      message.error(typeof detail === 'string' ? detail : '保存并停止失败')
      await loadJobs(false)
      throw e
    }
  }

  const handleForceStop = async (row: any) => {
    try {
      const res: any = await streamingApi.cancelJob(row.id)
      const lc = String(res?.lifecycle_state || '').toUpperCase()
      setJobs(prev => prev.map(j => (
        j.id === row.id
          ? {
              ...j,
              status: 'cancelled',
              lifecycle_state: lc || (res?.accepted ? 'FORCE_STOPPING' : 'FORCE_STOPPED'),
              flink_job_id: null,
            }
          : j
      )))
      if (lc === 'FORCE_STOPPING' || (res?.accepted && lc !== 'FORCE_STOPPED')) {
        message.success(res?.message || '已提交清理，资源回收中')
      } else {
        message.success(res?.message || '已清理集群')
      }
      await loadJobs(false)
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || '清理集群失败'
      message.error(typeof detail === 'string' ? detail : '清理集群失败')
      throw e
    }
  }

  const confirmStop = (row: any) => {
    Modal.confirm({
      title: '保存状态并停止作业？',
      width: 480,
      content: (
        <div>
          <p style={{ marginBottom: 8 }}>
            将先生成恢复点（等待 FlinkStateSnapshot），再挂起集群。成功后作业为「已停止」，可从恢复点重新启动。
          </p>
          <p style={{ marginBottom: 8, color: 'rgba(0,0,0,0.65)' }}>
            提交后按钮会暂时不可用，行状态为「正在保存状态」。大状态作业默认最长约
            {' '}{Math.round(STOP_SAVEPOINT_TIMEOUT_SECONDS / 60)} 分钟。请留在本页等待提示；成功变为「已停止」，失败回到「运行中」。
          </p>
          <p style={{ marginBottom: 0, color: 'rgba(0,0,0,0.65)' }}>
            中途刷新不会中断后台停止，但可能暂时看不到进度。也可打开「操作记录」查看。
          </p>
        </div>
      ),
      okText: '保存并停止',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => handleStop(row),
    })
  }

  const confirmForceStop = (row: any) => {
    let acknowledged = false
    Modal.confirm({
      title: '清理集群（丢弃状态）？',
      width: 480,
      content: (
        <div>
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 12 }}
            message="不会创建恢复点，本次停止后无法从状态续跑"
          />
          <p style={{ marginBottom: 12, color: 'rgba(0,0,0,0.65)' }}>
            将删除 FlinkDeployment 并回收 JM/TM。适用于僵尸作业、保存状态反复失败或需腾出资源。
            日常停机请使用「保存并停止」。
          </p>
          <Checkbox onChange={(e) => { acknowledged = e.target.checked }}>
            我确认丢弃状态，且了解无法从本次清理恢复
          </Checkbox>
        </div>
      ),
      okText: '清理集群',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => {
        if (!acknowledged) {
          message.warning('请先勾选确认丢弃状态')
          return Promise.reject()
        }
        return handleForceStop(row)
      },
    })
  }

  const applyReleaseRuntimeDefaults = (source: any) => {
    const config = parseStreamRuntimeConfig(source?.streaming_properties)
    setParallelism(Number(source?.parallelism) || 1)
    setAdvancedJson('{}')
    setResourceTier(config.resourceTier)
    setOperatorResources(config.operatorResources)
  }

  const openLifecycleAction = async (row: any, kind: 'deploy' | 'restart') => {
    const releases = releaseMap[row.id] || []
    const approved = releases.find(release => isApprovedNotDeployed(release, row))
    const selectedReleaseId = approved?.id ?? row.current_approved_release_id ?? row.latest_release_id ?? row.release_id
    const selectedRelease = releases.find(release => Number(release.id) === Number(selectedReleaseId))
    setActionRow(row)
    setActionKind(kind)
    setReleaseId(selectedReleaseId)
    applyReleaseRuntimeDefaults(selectedRelease || row)
    setRestoreMode('latest')
    setRestorePointId(undefined)
    setAllowNonRestoredState(false)
    setRestorePoints([])
    setActionOpen(true)
    if (kind === 'restart') {
      try {
        const value: any = await streamingApi.getRestorePoints(row.id)
        const points = asItems(value, 'restore_points')
        setRestorePoints(points)
        const hasCompleted = points.some((point: any) => (
          (!point.status || String(point.status).toLowerCase() === 'completed')
          && (!point.point_type || point.point_type === 'savepoint')
          && Boolean(point.path || point.location)
        ))
        const running = String(row.status || '').toLowerCase() === 'running'
          || String(row.lifecycle_state || '').toUpperCase() === 'RUNNING'
        if (!hasCompleted && !running) {
          setRestoreMode('stateless')
        } else {
          setRestoreMode('latest')
        }
      } catch {
        setRestorePoints([])
        const running = String(row.status || '').toLowerCase() === 'running'
          || String(row.lifecycle_state || '').toUpperCase() === 'RUNNING'
        setRestoreMode(running ? 'latest' : 'stateless')
      }
    }
  }

  const completedRestorePoints = useMemo(
    () => restorePoints.filter(point => (
      (!point.status || String(point.status).toLowerCase() === 'completed')
      && (!point.point_type || point.point_type === 'savepoint')
      && Boolean(point.path || point.location)
    )),
    [restorePoints],
  )

  /** 与后端一致：仍在跑且无恢复点时允许 latest → 热重启（restartNonce） */
  const canHotRestartWithoutSavepoint = useMemo(() => {
    if (!actionRow) return false
    const st = String(actionRow.status || '').toLowerCase()
    const lc = String(actionRow.lifecycle_state || '').toUpperCase()
    return st === 'running' || lc === 'RUNNING'
  }, [actionRow])

  const submitLifecycleAction = async () => {
    if (!actionRow) return
    let streamingProperties: string
    try {
      streamingProperties = buildStreamRuntimeProperties(advancedJson, operatorResources, resourceTier)
    } catch {
      message.error('高级 Flink 配置 JSON 格式无效')
      return
    }
    if (actionKind === 'restart' && restoreMode === 'specific' && restorePointId == null) {
      message.warning('请选择恢复点')
      return
    }
    if (
      actionKind === 'restart'
      && restoreMode === 'specific'
      && completedRestorePoints.length === 0
    ) {
      message.error(
        `「${actionRow.name}」没有可用的成功 Savepoint。请先对该作业「保存并停止」成功，或改用无状态启动。`,
      )
      return
    }
    if (
      actionKind === 'restart'
      && restoreMode === 'latest'
      && completedRestorePoints.length === 0
      && !canHotRestartWithoutSavepoint
    ) {
      message.error(
        `「${actionRow.name}」没有可用的成功 Savepoint。请先对该作业「保存并停止」成功，或改用无状态启动。`,
      )
      return
    }
    const config = {
      release_id: releaseId,
      parallelism,
      streaming_properties: streamingProperties,
    }
    const payload = actionKind === 'deploy'
      ? { ...config }
      : {
          ...config,
          restore_mode: restoreMode,
          restore_point_id: restoreMode === 'specific' ? restorePointId : undefined,
          allow_non_restored_state: allowNonRestoredState,
          confirm_stateless: restoreMode === 'stateless',
        }
    setActionLoading(true)
    const loadingKey = actionKind === 'deploy' ? 'stream-deploy' : 'stream-restart'
    message.loading({
      content: actionKind === 'deploy'
        ? '正在部署，等待作业 RUNNING（最长约 3 分钟）…'
        : '正在恢复，等待作业 RUNNING（最长约 3 分钟）…',
      key: loadingKey,
      duration: 0,
    })
    // 立刻反映到列表，避免弹窗长时间无反馈像「没点上」
    if (actionKind === 'restart') {
      setJobs(prev => prev.map(j => (
        j.id === actionRow.id
          ? { ...j, status: 'running', lifecycle_state: 'RESTORING' }
          : j
      )))
    } else {
      setJobs(prev => prev.map(j => (
        j.id === actionRow.id
          ? { ...j, status: 'running', lifecycle_state: 'DEPLOYING' }
          : j
      )))
    }
    try {
      const res: any = actionKind === 'deploy'
        ? await streamingApi.deployJob(actionRow.id, payload)
        : await streamingApi.restartJob(actionRow.id, payload)
      message.success({
        content: res?.message || (actionKind === 'deploy' ? '已提交部署' : '已提交重启/恢复'),
        key: loadingKey,
      })
      setActionOpen(false)
      await loadJobs()
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message
      const text = typeof detail === 'string'
        ? detail
        : (actionKind === 'deploy' ? '部署失败' : '重启失败')
      message.error({ content: text, key: loadingKey })
      await loadJobs(false)
    } finally {
      setActionLoading(false)
    }
  }

  const openRestorePoints = async (row: any) => {
    setOperationRow(row)
    setRestoreDrawerOpen(true)
    setDrawerLoading(true)
    try {
      const value: any = await streamingApi.getRestorePoints(row.id)
      setRestorePoints(asItems(value, 'restore_points'))
    } catch (e: any) {
      setRestorePoints([])
      message.error(e?.response?.data?.detail || '恢复点历史加载失败')
    } finally {
      setDrawerLoading(false)
    }
  }

  const openOperations = async (row: any) => {
    setOperationRow(row)
    setOperationDrawerOpen(true)
    setDrawerLoading(true)
    try {
      const value: any = await streamingApi.getOperations(row.id)
      setOperations(asItems(value, 'operations'))
    } catch (e: any) {
      setOperations([])
      message.error(e?.response?.data?.detail || '操作记录加载失败')
    } finally {
      setDrawerLoading(false)
    }
  }

  /** 轮询 Flink 回填库内 status；主表只展示统一作业状态，底层状态留在 tooltip / 诊断中。 */
  useEffect(() => {
    let alive = true
    let inFlight = false
    const poll = async () => {
      if (!wsId || !alive || inFlight) return
      const list = jobsRef.current
      if (!list.some(jobNeedsFlinkStatusPoll)) return
      inFlight = true
      try {
        const res: any = await streamingApi.syncJobsStatus(wsId)
        if (!alive) return
        const nextMap: Record<number, { flink_status?: string; status?: string; lifecycle_state?: string }> = {}
        for (const s of res?.items || []) {
          nextMap[s.id] = {
            flink_status: s.flink_status,
            status: s.status,
            lifecycle_state: s.lifecycle_state,
          }
        }
        setFlinkMap(prev => {
          let changed = false
          for (const [id, u] of Object.entries(nextMap)) {
            const key = Number(id)
            const old = prev[key]
            if (
              !old
              || old.status !== u.status
              || old.flink_status !== u.flink_status
              || old.lifecycle_state !== u.lifecycle_state
            ) {
              changed = true
              break
            }
          }
          return changed ? { ...prev, ...nextMap } : prev
        })
        // 回填 status + lifecycle：停止过程中 status 常一直是 running，必须靠 lifecycle 才能离开「保存中」
        setJobs(prev => {
          let changed = false
          const next = prev.map(j => {
            const u = nextMap[j.id]
            if (!u) return j
            const patch: Record<string, unknown> = {}
            if (u.status != null && u.status !== j.status) patch.status = u.status
            if (u.lifecycle_state != null && u.lifecycle_state !== j.lifecycle_state) {
              patch.lifecycle_state = u.lifecycle_state
            }
            if (!Object.keys(patch).length) return j
            changed = true
            return { ...j, ...patch }
          })
          return changed ? next : prev
        })
      } catch { /* ignore */ } finally {
        inFlight = false
      }
    }
    poll()
    const hasStopping = jobsRef.current.some(j => {
      const lc = String(j.lifecycle_state || '').toUpperCase()
      return lc === 'SAVING_STATE' || lc === 'SUSPENDING' || lc === 'FORCE_STOPPING'
    })
    const t = window.setInterval(poll, hasStopping ? 3000 : 8000)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [wsId, stoppingJobs.length])

  /** 保存并停止进行中：盯操作记录；仅在本页用短 toast 报结果，离开页面即停止提示 */
  useEffect(() => {
    if (!stoppingJobs.length) return
    let alive = true
    const tick = async () => {
      for (const row of stoppingJobs) {
        if (!alive) return
        try {
          const value: any = await streamingApi.getOperations(row.id)
          const ops = asItems(value, 'operations')
          const stopOp = ops.find((op: any) => String(op.operation_type || op.operation || '') === 'stop')
            || (row._pending_stop_operation_id != null
              ? ops.find((op: any) => Number(op.id) === Number(row._pending_stop_operation_id))
              : null)
          if (!stopOp) continue
          const opId = Number(stopOp.id)
          const st = String(stopOp.status || '').toLowerCase()
          if (st !== 'succeeded' && st !== 'failed') continue
          if (opId && notifiedStopOpsRef.current.has(opId)) continue
          if (opId) notifiedStopOpsRef.current.add(opId)
          if (st === 'succeeded') {
            message.success(`「${row.name}」已停止并保存状态，可从恢复点重启`)
          } else {
            message.error(
              `「${row.name}」保存并停止失败，作业应仍在运行。${stopOp.error_message || stopOp.error || ''}`.trim(),
            )
          }
          await loadJobs(false)
        } catch { /* ignore */ }
      }
    }
    void tick()
    const t = window.setInterval(() => { void tick() }, 3000)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [stoppingJobs])

  // 离开运维页时清掉可能残留的全局 stop loading（兼容旧版 duration:0）
  useEffect(() => () => {
    for (const row of jobsRef.current) {
      message.destroy(`stop-${row.id}`)
    }
  }, [])

  const unifiedJobState = (row: any) => {
    const platform = String(flinkMap[row.id]?.status || row.status || '').toLowerCase()
    const flink = String(flinkMap[row.id]?.flink_status || row.flink_status || '')
    const lifecycle = String(row.lifecycle_state || '').toUpperCase()
    const hasPendingApprovedRelease = (releaseMap[row.id] || []).some(release => isApprovedNotDeployed(release, row))
      || (row.current_approved_release_id
        && row.current_running_release_id
        && Number(row.current_approved_release_id) !== Number(row.current_running_release_id))
      || (row.approval_status === 'approved' && !row.deployed_at && !row.flink_operator_deployment_name
        && !row.current_running_release_id)

    const transitions: Record<string, { key: string; label: string; color: string }> = {
      SAVING_STATE: { key: 'active', label: '正在保存状态', color: 'processing' },
      SUSPENDING: { key: 'active', label: '正在挂起', color: 'processing' },
      DEPLOYING: { key: 'active', label: '正在部署', color: 'processing' },
      RESTORING: { key: 'active', label: '正在恢复', color: 'processing' },
      SUSPENDED: {
        key: 'stopped',
        label: hasPendingApprovedRelease ? '已停止 · 有待部署版本' : '已停止',
        color: 'warning',
      },
      RESTORE_FAILED: { key: 'needs_attention', label: '恢复失败', color: 'error' },
      DEPLOY_FAILED: { key: 'needs_attention', label: '部署失败', color: 'error' },
      STOP_FAILED: { key: 'needs_attention', label: '停止未完成', color: 'error' },
      FORCE_STOPPING: { key: 'active', label: '正在清理集群', color: 'processing' },
      FORCE_STOP_FAILED: { key: 'needs_attention', label: '清理未完成', color: 'error' },
      FORCE_STOPPED: {
        key: 'stopped',
        label: hasPendingApprovedRelease ? '已停止（已清理）· 有待部署版本' : '已停止（已清理）',
        color: 'warning',
      },
    }
    if (transitions[lifecycle]) {
      if (
        (lifecycle === 'STOP_FAILED'
          || lifecycle === 'DEPLOY_FAILED'
          || lifecycle === 'RESTORE_FAILED'
          || lifecycle === 'FORCE_STOP_FAILED')
        && (platform === 'running' || /RUNNING|STABLE/i.test(flink))
      ) {
        return {
          key: 'active',
          label: hasPendingApprovedRelease ? '运行中 · 有新版本可部署' : '运行中',
          color: 'processing',
        }
      }
      return transitions[lifecycle]
    }

    const stoppedByCluster = /NOT_FOUND_ON_OPERATOR|SUSPENDED/i.test(flink)
      || platform === 'cancelled'
      || /CANCEL|NOT_FOUND_ON_JM/i.test(flink)
    if (stoppedByCluster) {
      return {
        key: 'stopped',
        label: hasPendingApprovedRelease ? '已停止 · 有待部署版本' : '已停止',
        color: 'warning',
      }
    }
    if (/JM_UNREACHABLE/i.test(flink)) {
      return { key: 'needs_attention', label: 'JM 不可达', color: 'error' }
    }
    // 运行中优先于陈旧 last_submit_error：集群已健康时勿再标「需处理」
    if (platform === 'running' || lifecycle === 'RUNNING' || /RUNNING|STABLE/i.test(flink)) {
      return {
        key: 'active',
        label: hasPendingApprovedRelease ? '运行中 · 有新版本可部署' : '运行中',
        color: 'processing',
      }
    }
    if (row.last_submit_error || platform === 'failed' || /FAILED|RESTORE_FAILED/i.test(flink)) {
      return { key: 'needs_attention', label: '需处理', color: 'error' }
    }
    // 运行中/启动中优先于「待部署」，避免停完或仍在跑时被误标成已批准待部署
    if (/DEPLOY|START|INITIALIZING|CREATED|PENDING/i.test(flink) || lifecycle === 'DEPLOYING' || lifecycle === 'RESTORING') {
      return {
        key: 'active',
        label: lifecycle === 'RESTORING' ? '正在恢复' : '启动中',
        color: 'processing',
      }
    }
    if (platform === 'finished' || /FINISHED/i.test(flink)) {
      return { key: 'terminal', label: '已结束', color: 'success' }
    }
    if (hasPendingApprovedRelease || (row.approval_status === 'approved' && !row.deployed_at && !row.flink_operator_deployment_name)) {
      return { key: 'ready_to_deploy', label: '已批准待部署', color: 'cyan' }
    }
    if (platform === 'draft' || lifecycle === 'DRAFT' || lifecycle === 'APPROVED' || lifecycle === 'PENDING_APPROVAL') {
      if (lifecycle === 'APPROVED' || lifecycle === 'PENDING_APPROVAL') {
        return { key: 'ready_to_deploy', label: lifecycle === 'PENDING_APPROVAL' ? '待审批' : '已批准待部署', color: 'cyan' }
      }
      return { key: 'draft', label: '草稿', color: 'default' }
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
        row.pipeline_spec?.source?.topic,
        row.pipeline_spec?.sink?.table,
      ].filter(Boolean).join(' ').toLowerCase()
      return hay.includes(kw)
    })
  }, [jobs, flinkMap, keyword, typeFilter, deployFilter, stateFilter, releaseMap])

  const renderUnifiedState = (row: any) => {
    const platform = flinkMap[row.id]?.status || row.status
    const flink = flinkMap[row.id]?.flink_status
    const state = unifiedJobState(row)
    return (
      <Tooltip title={`生命周期：${row.lifecycle_state || '—'}；平台记录：${PLATFORM_STATUS_LABEL[platform] || platform || '—'}；Flink 原始状态：${flink ? (FLINK_STATUS_LABEL[flink] || flink) : '—'}`}>
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
    {
      title: '类型',
      dataIndex: 'job_type',
      key: 'job_type',
      width: 120,
      render: (t: string, row: any) => row.definition_kind === 'pipeline'
        ? <Space size={4}><Tag color="cyan">Pipeline</Tag><Tag>{String(row.pipeline_spec?.mode || 'append').toUpperCase()}</Tag></Space>
        : <Tag>{t}</Tag>,
    },
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
      title: '发布版本',
      key: 'release',
      width: 130,
      render: (_: unknown, row: any) => {
        const releases = releaseMap[row.id] || []
        const latest = releases[0] || row.latest_release
        if (!latest) return <Text type="secondary">—</Text>
        return (
          <Space size={4} wrap>
            <Text code>{latest.version != null ? `v${latest.version}` : `#${latest.id || '—'}`}</Text>
            <Tag color={isApprovedNotDeployed(latest, row) ? 'cyan' : undefined}>{releaseStatus(latest) || '未知'}</Tag>
          </Space>
        )
      },
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
    {
      title: '历史',
      key: 'history',
      width: 100,
      render: (_: unknown, row: any) => (
        <Space size={0}>
          <Tooltip title="恢复点历史">
            <Button type="text" size="small" icon={<HistoryOutlined />} onClick={() => openRestorePoints(row)} />
          </Tooltip>
          <Tooltip title="操作记录">
            <Button type="text" size="small" icon={<RetweetOutlined />} onClick={() => openOperations(row)} />
          </Tooltip>
        </Space>
      ),
    },
    ...(canRun ? [{
      title: '生命周期操作',
      key: 'lifecycle-actions',
      fixed: 'right' as const,
      width: 300,
      render: (_: unknown, row: any) => {
        const state = unifiedJobState(row).key
        const lifecycle = String(row.lifecycle_state || '').toUpperCase()
        const stopping = lifecycle === 'SAVING_STATE' || lifecycle === 'SUSPENDING'
        const cleaning = lifecycle === 'FORCE_STOPPING'
        const approved = (releaseMap[row.id] || []).some(release => isApprovedNotDeployed(release, row))
          || (row.current_approved_release_id && row.current_approved_release_id !== row.current_running_release_id)
          || (row.approval_status === 'approved' && !row.deployed_at)
        const active = state === 'active'
        // 失败/需处理时平台可能仍挂着 FlinkDeployment，必须允许停止/清理，否则部署会被 409 卡住
        const canStop = !stopping && !cleaning && (active
          || state === 'needs_attention'
          || Boolean(isOperatorJob(row) && row.flink_operator_deployment_name))
        const canRestart = !stopping && !cleaning && (active || state === 'stopped' || state === 'needs_attention')
        const canForceClear = (canStop || stopping) && !cleaning
        return (
          <Space size={4} wrap>
            <Button size="small" type={approved ? 'primary' : 'default'} icon={<RocketOutlined />}
              disabled={active || stopping || (!approved && !row.current_approved_release_id && !row.latest_release_id && !(releaseMap[row.id] || []).length)}
              onClick={() => void openLifecycleAction(row, 'deploy')}>
              部署
            </Button>
            <Button size="small" icon={<RetweetOutlined />} disabled={!canRestart}
              onClick={() => void openLifecycleAction(row, 'restart')}>
              重启/恢复
            </Button>
            <Tooltip
              title={
                stopping
                  ? '正在等待 FlinkStateSnapshot / Savepoint，请稍候或打开操作记录'
                  : `先生成恢复点再停止（默认最长约 ${Math.round(STOP_SAVEPOINT_TIMEOUT_SECONDS / 60)} 分钟）`
              }
            >
              <Button size="small" danger disabled={!canStop} icon={<StopOutlined />}
                onClick={() => confirmStop(row)}>{stopping ? '保存中…' : '保存并停止'}</Button>
            </Tooltip>
            <Dropdown
              menu={{
                items: [{
                  key: 'force-clear',
                  danger: true,
                  disabled: !canForceClear,
                  label: '清理集群（丢弃状态）',
                  onClick: () => confirmForceStop(row),
                }],
              }}
              trigger={['click']}
            >
              <Button size="small" type="text" icon={<MoreOutlined />} aria-label="更多运维操作" />
            </Dropdown>
          </Space>
        )
      },
    }] : []),
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <div>
          <Typography.Title level={4} style={{ marginBottom: 4 }}>作业运维</Typography.Title>
          <Paragraph type="secondary" style={{ marginBottom: 0, maxWidth: 920 }}>
            部署运行中的作业；默认「保存并停止」会生成恢复点，失败时作业仍保持运行。
            清理集群（丢弃状态）在「更多」中。诊断与 Flink UI 见行内入口；逻辑编辑请到
            {' '}<Link to={R.stream.studio}>作业开发</Link>
            ，Source → Paimon 标准链路请到
            {' '}<Link to={R.stream.pipelines}>数据管道</Link>
            ，依赖包请到
            {' '}<Link to={R.stream.resources}>资源管理</Link>。
          </Paragraph>
        </div>
      </div>

      {overviewErr && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="FlinkDeployment 概览加载失败" description={overviewErr} />
      )}

      {stoppingJobs.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`正在保存状态并停止：${stoppingJobs.map(j => j.name).join('、')}`}
          description={`已受理，正在等待 FlinkStateSnapshot / Savepoint 完成（默认最长约 ${Math.round(STOP_SAVEPOINT_TIMEOUT_SECONDS / 60)} 分钟）。进度只显示在本页；成功后变为「已停止」，失败则回到「运行中」。可点历史里的操作记录查看详情。`}
        />
      )}
      {cleaningJobs.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`正在清理集群：${cleaningJobs.map(j => j.name).join('、')}`}
          description="已请求删除 FlinkDeployment，正在等待资源完全回收（卡住时平台会尝试解除 finalizer）。完成后变为「已停止（已清理）」。"
        />
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
            <Text strong>作业开发 / 数据管道</Text>
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
                        { value: 'ready_to_deploy', label: '已批准待部署' },
                        { value: 'draft', label: '草稿' },
                        { value: 'needs_attention', label: '需处理' },
                      ]}
                    />
                    <Text type="secondary">共 {filteredJobs.length} / {jobs.length} 个作业</Text>
                  </Space>
                </Card>
                <Table rowKey="id" loading={loading} dataSource={filteredJobs} columns={columns as any} scroll={{ x: 1750 }} pagination={{ pageSize: 12 }} />
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

      <Modal
        title={`${actionKind === 'deploy' ? '部署发布版本' : '重启 / 恢复'} · ${actionRow?.name || ''}`}
        open={actionOpen}
        onCancel={() => setActionOpen(false)}
        onOk={submitLifecycleAction}
        okText={actionKind === 'deploy' ? '确认部署' : '确认重启'}
        confirmLoading={actionLoading}
        width={720}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={actionKind === 'deploy'
            ? '部署已批准且尚未部署的发布版本'
            : '默认从最近成功 Savepoint 恢复；普通 Checkpoint 仅用于故障恢复和诊断'}
        />
        <Form layout="vertical">
          <Form.Item label="发布版本">
            <Select
              allowClear
              value={releaseId}
              onChange={value => {
                setReleaseId(value)
                const release = (actionRow ? releaseMap[actionRow.id] || [] : [])
                  .find((item: any) => Number(item.id) === Number(value))
                if (release) applyReleaseRuntimeDefaults(release)
              }}
              placeholder="后端未返回版本时使用作业当前发布定义"
              options={(actionRow ? releaseMap[actionRow.id] || [] : []).map((release: any) => ({
                value: release.id,
                label: `${release.version != null ? `v${release.version}` : `#${release.id}`} · ${releaseStatus(release) || '未知'}`,
                disabled: actionKind === 'deploy' && !isApprovedNotDeployed(release, actionRow),
              }))}
            />
          </Form.Item>
          <Form.Item label="并行度">
            <InputNumber min={1} value={parallelism} onChange={value => setParallelism(Number(value) || 1)} style={{ width: '100%' }} />
          </Form.Item>
          {actionKind === 'restart' && (
            <>
              {completedRestorePoints.length === 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message={`「${actionRow?.name || '当前作业'}」暂无成功恢复点`}
                  description={
                    canHotRestartWithoutSavepoint
                      ? '作业仍在运行：可选择「热重启（同配置，无需恢复点）」；或改用无状态启动。恢复点按作业隔离。'
                      : '恢复点按作业隔离。请确认打开的是做过「保存并停止」且成功的那条作业；或改用无状态启动。'
                  }
                />
              ) : (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message={`「${actionRow?.name || '当前作业'}」可用成功恢复点 ${completedRestorePoints.length} 个`}
                />
              )}
              <Form.Item label="恢复方式">
                <Radio.Group value={restoreMode} onChange={e => setRestoreMode(e.target.value)}>
                  <Space direction="vertical">
                    <Radio
                      value="latest"
                      disabled={completedRestorePoints.length === 0 && !canHotRestartWithoutSavepoint}
                    >
                      {completedRestorePoints.length === 0 && canHotRestartWithoutSavepoint
                        ? '热重启（同配置，无需恢复点）'
                        : '最近可用恢复点'}
                    </Radio>
                    <Radio value="specific" disabled={completedRestorePoints.length === 0}>
                      指定恢复点
                    </Radio>
                    <Radio value="last-state">
                      最近状态（last-state，快速有状态）
                    </Radio>
                    <Radio value="stateless">无状态启动</Radio>
                  </Space>
                </Radio.Group>
              </Form.Item>
              {restoreMode === 'last-state' && (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="从 Operator HA / 最近 Checkpoint 恢复"
                  description="比 Savepoint 更快，适合健康作业的快速重启；不依赖平台恢复点历史。需集群已启用 Checkpoint 与 HA。"
                />
              )}
              {restoreMode === 'specific' && (
                <Form.Item label="恢复点">
                  <Select
                    value={restorePointId}
                    onChange={setRestorePointId}
                    placeholder={completedRestorePoints.length ? '选择成功 Savepoint' : '暂无可用恢复点'}
                    options={completedRestorePoints.map(point => ({
                      value: point.id ?? point.path ?? point.location,
                      label: `${point.point_type || point.type || point.kind || 'Savepoint'} · ${point.path || point.location || point.id || '—'}`,
                    }))}
                  />
                </Form.Item>
              )}
              {restoreMode === 'stateless' && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="无状态启动会丢弃已有状态"
                  description="点击“确认重启”即表示明确接受从头启动；该操作会写入运维审计。"
                />
              )}
              <Form.Item label="高级恢复选项">
                <Space>
                  <Switch checked={allowNonRestoredState} onChange={setAllowNonRestoredState} />
                  <span>允许未恢复状态（allowNonRestoredState）</span>
                </Space>
                <Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 0 }}>
                  默认关闭。仅在确认作业拓扑变更导致旧状态无法映射时开启。
                </Paragraph>
              </Form.Item>
            </>
          )}
          <StreamRuntimeConfig
            resourceTier={resourceTier}
            onResourceTierChange={setResourceTier}
            operatorResources={operatorResources}
            onOperatorResourcesChange={setOperatorResources}
            advancedJson={advancedJson}
            onAdvancedJsonChange={setAdvancedJson}
            showAdvanced={false}
          />
        </Form>
      </Modal>

      <Drawer
        title={`恢复点历史 · ${operationRow?.name || ''}`}
        open={restoreDrawerOpen}
        onClose={() => setRestoreDrawerOpen(false)}
        width={860}
      >
        <Table
          rowKey={(row: any) => row.id ?? row.path ?? row.location}
          loading={drawerLoading}
          dataSource={restorePoints}
          pagination={{ pageSize: 10 }}
          scroll={{ x: 1100 }}
          columns={[
            { title: '类型', key: 'type', width: 110, render: (_: unknown, row: any) => <Tag>{row.point_type || row.type || row.kind || '未知'}</Tag> },
            { title: '状态', key: 'status', width: 110, render: (_: unknown, row: any) => row.status ? <Tag>{row.status}</Tag> : '—' },
            { title: '路径', key: 'path', ellipsis: true, render: (_: unknown, row: any) => <Text code>{row.path || row.location || row.external_pointer || '—'}</Text> },
            { title: '发布版本', key: 'release', width: 100, render: (_: unknown, row: any) => row.release_id ? `#${row.release_id}` : '—' },
            { title: '并行度', key: 'parallelism', width: 86, render: (_: unknown, row: any) => parseJsonObject(row.metadata_json).parallelism ?? '—' },
            { title: '创建时间', key: 'time', width: 180, render: (_: unknown, row: any) => formatInTimeZone(row.created_at || row.completed_at || row.timestamp, displayTz) },
            {
              title: '执行时间',
              key: 'duration',
              width: 110,
              render: (_: unknown, row: any) => {
                const seconds = restorePointDurationSeconds(row)
                if (seconds != null) return formatElapsedSeconds(seconds)
                const st = String(row.status || '').toLowerCase()
                if (st === 'pending' || st === 'running' || st === 'in_progress') return '进行中'
                return '—'
              },
            },
            { title: '错误', key: 'error', ellipsis: true, render: (_: unknown, row: any) => row.error_message || '—' },
          ] as any}
          locale={{ emptyText: '暂无恢复点，或后端尚未返回 restore_points 字段' }}
        />
      </Drawer>

      <Drawer
        title={`操作记录 · ${operationRow?.name || ''}`}
        open={operationDrawerOpen}
        onClose={() => setOperationDrawerOpen(false)}
        width={820}
      >
        <Table
          rowKey={(row: any) => row.id ?? `${row.operation || row.action}-${row.created_at || row.started_at}`}
          loading={drawerLoading}
          dataSource={operations}
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '操作',
              key: 'operation',
              width: 130,
              render: (_: unknown, row: any) => {
                const raw = String(row.operation_type || row.operation || row.action || row.type || '')
                return OPERATION_TYPE_LABEL[raw] || raw || '—'
              },
            },
            {
              title: '状态',
              key: 'status',
              width: 110,
              render: (_: unknown, row: any) => {
                const st = String(row.status || '')
                if (!st) return '—'
                const color = st === 'succeeded' ? 'success' : st === 'failed' ? 'error' : st === 'running' ? 'processing' : 'default'
                return <Tag color={color}>{OPERATION_STATUS_LABEL[st] || st}</Tag>
              },
            },
            { title: '操作人', key: 'user', width: 120, render: (_: unknown, row: any) => row.operator_username || row.created_by_username || row.username || row.requested_by || '—' },
            { title: '时间', key: 'time', width: 180, render: (_: unknown, row: any) => formatInTimeZone(row.requested_at || row.created_at || row.started_at, displayTz) },
            { title: '详情', key: 'detail', ellipsis: true, render: (_: unknown, row: any) => row.message || row.detail || row.error_message || row.error || '—' },
          ] as any}
          locale={{ emptyText: '暂无操作记录，或后端尚未返回 operations 字段' }}
        />
      </Drawer>

      <Drawer
        title={diagRow ? `诊断 · ${diagRow.name}` : '诊断'}
        width={720}
        open={diagOpen}
        onClose={() => {
          setDiagOpen(false)
          setDiagRow(null)
          setDiagExceptions(null)
          setDiagSync(null)
          setDiagObservability(null)
        }}
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

        {diagSync?.failure ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>结构化失败原因</Typography.Title>
            <Alert
              type="error"
              showIcon
              message={`来源：${diagSync.failure.source || 'unknown'} · 阶段：${diagSync.failure.phase || '—'}`}
              description={
                <pre style={{
                  margin: 0,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontSize: 12,
                  maxHeight: 200,
                  overflow: 'auto',
                }}>
                  {diagSync.failure.message}
                </pre>
              }
            />
          </>
        ) : null}

        <Typography.Title level={5} style={{ marginTop: 16 }}>Checkpoint 摘要</Typography.Title>
        {!diagCheckpoints ? (
          <Text type="secondary">加载中…</Text>
        ) : !diagCheckpoints.available ? (
          <Text type="secondary">{diagCheckpoints.reason || '暂不可用（JobManager 不可达或尚无 Job ID）'}</Text>
        ) : (
          <Descriptions size="small" bordered column={1} style={{ marginBottom: 8 }}>
            <Descriptions.Item label="完成 / 失败 / 进行中">
              {diagCheckpoints.counts?.completed ?? '—'} / {diagCheckpoints.counts?.failed ?? '—'} / {diagCheckpoints.counts?.in_progress ?? '—'}
            </Descriptions.Item>
            <Descriptions.Item label="最近成功">
              {diagCheckpoints.latest_completed?.id != null
                ? `#${diagCheckpoints.latest_completed.id} · ${diagCheckpoints.latest_completed.duration_ms ?? '—'}ms · ${diagCheckpoints.latest_completed.path || '—'}`
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="最近失败">
              {diagCheckpoints.latest_failed?.id != null
                ? `#${diagCheckpoints.latest_failed.id} · ${diagCheckpoints.latest_failed.failure_message || '—'}`
                : '—'}
            </Descriptions.Item>
          </Descriptions>
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

        {diagRow?.definition_kind === 'pipeline' ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>Pipeline 实时指标</Typography.Title>
            {!diagObservability ? (
              <Text type="secondary">加载中…</Text>
            ) : diagObservability.error ? (
              <Alert type="warning" showIcon message="指标暂不可用"
                description={String(diagObservability.error)} />
            ) : (
              <Descriptions size="small" bordered column={1}>
                {(diagObservability.observations || []).map((observation: any) => (
                  <Descriptions.Item key={observation.source}
                    label={`${String(observation.source).toUpperCase()} · ${observation.status}`}>
                    {observation.status === 'unavailable'
                      ? (observation.error || '暂不可用')
                      : <pre style={{ margin: 0, whiteSpace: 'pre-wrap', maxHeight: 180, overflow: 'auto' }}>
                          {JSON.stringify(observation.data, null, 2)}
                        </pre>}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            )}
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

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import {
  Table, Button, Space, Tag, message, Modal, Form, Input, InputNumber, Select, Upload, Card, Drawer,
  Divider, Typography, Alert, notification, Collapse, Popconfirm, Tooltip,
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, StopOutlined, SaveOutlined, ReloadOutlined, UploadOutlined, DeleteOutlined,
  UnlockOutlined, HistoryOutlined, CopyOutlined, SearchOutlined, EditOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, AimOutlined, ExpandAltOutlined,
} from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { streamingApi, approvalApi } from '../api'
import { useAppStore } from '../store'
import { isWorkspaceAdmin } from '../perm'
import PublishApprovalModal from '../components/PublishApprovalModal'
import { approvalPendingKey } from '../approvalLabels'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableSidebar from '../components/ResizableSidebar'
import QueryResultPanel from '../components/QueryResultPanel'
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { R } from '../routes'
import { Link } from 'react-router-dom'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import { formatInTimeZone } from '../utils/datetime'
import { openFlinkConsoleUrl } from '../utils/flinkConsole'
import AutosaveStatusHint from '../components/AutosaveStatusHint'
import { useScriptAutosave } from '../hooks/useScriptAutosave'
import {
  clearScriptLocalDraft,
  restoreScriptLocalDraft,
  scriptDraftStorageKey,
  writeScriptLocalDraft,
} from '../utils/scriptLocalDraft'

const { Paragraph, Text } = Typography
const STREAM_JOB_NAME_RULE = '3-50 位小写字母、数字、短横线，字母开头，字母或数字结尾，例如 s3-copy-users'
const STREAM_JOB_NAME_PATTERN = /^[a-z][a-z0-9-]{1,48}[a-z0-9]$/

const JOB_TYPES = [
  { label: 'Flink SQL', value: 'SQL' },
  { label: 'JAR 作业', value: 'JAR' },
]

type SqlSubmitMode = 'session' | 'kubernetes_application' | 'flink_operator'

type OperatorResForm = {
  jm_memory: string
  jm_cpu: string
  tm_memory: string
  tm_cpu: string
  task_slots: string
  tm_replicas: string
}

const EMPTY_OPERATOR_RES: OperatorResForm = {
  jm_memory: '',
  jm_cpu: '',
  tm_memory: '',
  tm_cpu: '',
  task_slots: '',
  tm_replicas: '',
}

function parseResourceTier(sp: unknown): string {
  if (sp == null || String(sp).trim() === '') return ''
  try {
    const t = JSON.parse(String(sp))?.resource_tier
    return t != null ? String(t) : ''
  } catch {
    return ''
  }
}

function parseOperatorResForm(sp: unknown): OperatorResForm {
  if (sp == null || String(sp).trim() === '') return { ...EMPTY_OPERATOR_RES }
  try {
    const obj = JSON.parse(String(sp))
    const or = obj?.operator_resources || {}
    return {
      jm_memory: or.jobManager?.memory != null ? String(or.jobManager.memory) : '',
      jm_cpu: or.jobManager?.cpu != null ? String(or.jobManager.cpu) : '',
      tm_memory: or.taskManager?.memory != null ? String(or.taskManager.memory) : '',
      tm_cpu: or.taskManager?.cpu != null ? String(or.taskManager.cpu) : '',
      task_slots: or.taskSlots != null ? String(or.taskSlots) : (or.numberOfTaskSlots != null ? String(or.numberOfTaskSlots) : ''),
      tm_replicas: or.taskManager?.replicas != null ? String(or.taskManager.replicas) : '',
    }
  } catch {
    return { ...EMPTY_OPERATOR_RES }
  }
}

function buildStreamingPropertiesJson(
  rawJson: string,
  operatorForm: OperatorResForm,
  includeOperatorRes: boolean,
  resourceTier?: string,
): string {
  let base: Record<string, unknown> = {}
  const trimmed = rawJson.trim()
  if (trimmed && trimmed !== '{}') {
    base = JSON.parse(trimmed)
    if (typeof base !== 'object' || base === null || Array.isArray(base)) {
      throw new Error('invalid')
    }
  }
  if (includeOperatorRes) {
    const tier = (resourceTier || '').trim()
    if (tier) base.resource_tier = tier
    else delete base.resource_tier
    const or: Record<string, unknown> = {}
    const jm: Record<string, unknown> = {}
    const tm: Record<string, unknown> = {}
    if (operatorForm.jm_memory.trim()) jm.memory = operatorForm.jm_memory.trim()
    if (operatorForm.jm_cpu.trim()) jm.cpu = Number(operatorForm.jm_cpu)
    if (operatorForm.tm_memory.trim()) tm.memory = operatorForm.tm_memory.trim()
    if (operatorForm.tm_cpu.trim()) tm.cpu = Number(operatorForm.tm_cpu)
    if (operatorForm.tm_replicas.trim()) tm.replicas = Number(operatorForm.tm_replicas)
    if (Object.keys(jm).length) or.jobManager = jm
    if (Object.keys(tm).length) or.taskManager = tm
    if (operatorForm.task_slots.trim()) or.taskSlots = Number(operatorForm.task_slots)
    if (Object.keys(or).length) base.operator_resources = or
    else delete base.operator_resources
  }
  if (!Object.keys(base).length) return ''
  return JSON.stringify(base)
}

function sqlModeLabel(mode: string | undefined) {
  const m = (mode || 'flink_operator').toLowerCase()
  if (m === 'kubernetes_application') return 'K8s Application'
  if (m === 'flink_operator') return 'Flink Operator'
  return 'Session'
}

function cdcPaimonSqlTemplate(warehouse: string) {
  const wh = warehouse || 's3://gido-paimon-warehouse'
  return `-- MySQL CDC → Paimon（GIDO 统一运行时 · Flink Operator · EKS 生产）
-- 前置：RDS/Aurora 开启 binlog(ROW)；Flink Pod 网络可达 MySQL:3306；
--       Paimon warehouse 为 s3://（runtime 含 flink-s3-fs-hadoop 插件 + IRSA）。
-- 密码：生产请改为真实值，或通过运维 Secret 注入后替换下方占位符。

CREATE TABLE mysql_orders (
  order_id BIGINT,
  user_id BIGINT,
  amount DECIMAL(10, 2),
  updated_at TIMESTAMP(3),
  PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = 'your-mysql.cluster-xxxxx.ap-northeast-1.rds.amazonaws.com',
  'port' = '3306',
  'username' = 'cdc_user',
  'password' = '***',
  'database-name' = 'shop',
  'table-name' = 'orders',
  'server-id' = '5400-5404',
  'scan.startup.mode' = 'initial',
  'server-time-zone' = 'Asia/Shanghai'
);

CREATE CATALOG paimon WITH (
  'type' = 'paimon',
  'warehouse' = '${wh}'
);

USE CATALOG paimon;

CREATE TABLE IF NOT EXISTS ods.orders (
  order_id BIGINT,
  user_id BIGINT,
  amount DECIMAL(10, 2),
  updated_at TIMESTAMP(3),
  PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

INSERT INTO ods.orders
SELECT order_id, user_id, amount, updated_at FROM default_catalog.default_database.mysql_orders;
`
}

export default function StreamStudioPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [jobs, setJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<any | null>(null)
  const [scriptDraft, setScriptDraft] = useState('')
  const [scriptDirty, setScriptDirty] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm()
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameForm] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const editorRef = useRef<any>(null)
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())
  const [jarForm, setJarForm] = useState({ main_class: '', program_args: '', parallelism: 1 })
  const [programArgsExpandOpen, setProgramArgsExpandOpen] = useState(false)
  const [sqlParallelism, setSqlParallelism] = useState(1)
  /** Flink SQL Gateway Open Session 合并用 JSON（对标阿里云实时计算「参数调优」的轻量版） */
  const [streamingPropsJson, setStreamingPropsJson] = useState('{}')
  const [flinkRuntime, setFlinkRuntime] = useState<any | null>(null)
  /** 终态产品：仅 Flink Operator；提交模式不再在 UI 暴露 */
  const [sqlSubmitMode] = useState<SqlSubmitMode>('flink_operator')
  const [operatorResForm, setOperatorResForm] = useState<OperatorResForm>({ ...EMPTY_OPERATOR_RES })
  const [resourceTier, setResourceTier] = useState<string>('')
  const [jarStreamingPropsJson, setJarStreamingPropsJson] = useState('{}')
  const [jarSubmitMode] = useState<'session' | 'flink_operator'>('flink_operator')
  const [historyModal, setHistoryModal] = useState(false)
  const [historyList, setHistoryList] = useState<any[]>([])
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set())
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approvalNote, setApprovalNote] = useState('')
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewResult, setPreviewResult] = useState<any | null>(null)
  const [previewLimit, setPreviewLimit] = useState(100)
  const [submitDrawerOpen, setSubmitDrawerOpen] = useState(false)
  const [resultPanelOpen, setResultPanelOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gido.streamStudio.sidebarCollapsed') === '1'
    } catch {
      return false
    }
  })
  const [resultPanelHeight, setResultPanelHeight] = useState(() => {
    try {
      const v = Number(localStorage.getItem('gido.streamStudio.resultPanelHeight'))
      if (Number.isFinite(v) && v >= 180 && v <= 720) return v
    } catch {
      /* ignore */
    }
    return 300
  })
  const resultPanelHeightRef = useRef(resultPanelHeight)
  resultPanelHeightRef.current = resultPanelHeight
  const resultResizeRef = useRef<{ startY: number; startHeight: number } | null>(null)

  const onResultResizeMove = useCallback((e: MouseEvent) => {
    const d = resultResizeRef.current
    if (!d) return
    const next = Math.min(720, Math.max(180, d.startHeight - (e.clientY - d.startY)))
    resultPanelHeightRef.current = next
    setResultPanelHeight(next)
  }, [])

  const onResultResizeUp = useCallback(() => {
    if (!resultResizeRef.current) return
    resultResizeRef.current = null
    try {
      localStorage.setItem('gido.streamStudio.resultPanelHeight', String(resultPanelHeightRef.current))
    } catch {
      /* ignore */
    }
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    window.removeEventListener('mousemove', onResultResizeMove)
    window.removeEventListener('mouseup', onResultResizeUp)
  }, [onResultResizeMove])

  const startResultResize = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    resultResizeRef.current = { startY: e.clientY, startHeight: resultPanelHeightRef.current }
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('mousemove', onResultResizeMove)
    window.addEventListener('mouseup', onResultResizeUp)
  }

  const setSidebarCollapsedPersist = (collapsed: boolean) => {
    setSidebarCollapsed(collapsed)
    try {
      localStorage.setItem('gido.streamStudio.sidebarCollapsed', collapsed ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  const locateSelectedJob = () => {
    if (!selected) {
      message.info('请先选择作业')
      return
    }
    setSidebarCollapsedPersist(false)
    window.setTimeout(() => {
      const el = document.querySelector(
        `.stream-job-list tr[data-stream-job-id="${selected.id}"]`,
      ) as HTMLElement | null
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }, 80)
  }

  const load = useCallback(async (showSpinner = true) => {
    if (!wsId) return
    if (showSpinner) setLoading(true)
    try {
      const [list, pendingRes]: any = await Promise.all([
        streamingApi.listJobs(wsId),
        approvalApi.list(wsId, { status: 'pending', page_size: 200 }),
      ])
      setJobs(list)
      setPendingKeys(
        new Set((pendingRes?.items || []).map((i: any) => approvalPendingKey(i.resource_type, i.resource_id, i.action))),
      )
      setSelected((prev) => {
        if (!prev) return prev
        const fresh = list.find((j: any) => j.id === prev.id)
        return fresh ?? prev
      })
    } finally {
      if (showSpinner) setLoading(false)
    }
  }, [wsId])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    streamingApi.flinkRuntime().then(setFlinkRuntime).catch(() => setFlinkRuntime(null))
  }, [])

  const effectiveSqlMode: SqlSubmitMode = 'flink_operator'
  const effectiveJarMode = 'flink_operator' as const

  /** Flink 控制台停止后 JM 已无作业时，单靠列表会卡在 running — 周期性拉 JM 回填平台状态（不打断编辑） */
  useEffect(() => {
    let alive = true
    const tick = async () => {
      if (!wsId || !alive) return
      try {
        const list = (await streamingApi.listJobs(wsId)) as unknown as any[]
        const tracked = list.filter(
          (j: any) =>
            j.flink_job_id
            || j.flink_application_cluster_id
            || ((j.flink_sql_submit_mode || j.flink_jar_submit_mode) === 'flink_operator'
              && j.flink_operator_deployment_name
              && j.status !== 'draft'),
        )
        if (tracked.length === 0) return
        await Promise.all(tracked.map((j: any) => streamingApi.getStatus(j.id).catch(() => null)))
        if (!alive) return
        await load(false)
      } catch { /* ignore */ }
    }
    const iv = window.setInterval(tick, 6500)
    tick()
    return () => {
      alive = false
      window.clearInterval(iv)
    }
  }, [wsId, load])

  const selectedIdRef = useRef<number | null>(null)
  selectedIdRef.current = selected?.id ?? null
  const scriptDraftRef = useRef(scriptDraft)
  scriptDraftRef.current = scriptDraft
  const scriptDirtyRef = useRef(scriptDirty)
  scriptDirtyRef.current = scriptDirty
  const prevStreamJobIdRef = useRef<number | null>(null)

  // 切换作业：先冲刷上一 SQL 草稿，再绑定编辑器（避免丢稿 / 串写到新作业）
  useEffect(() => {
    const prevId = prevStreamJobIdRef.current
    const currId = selected?.id ?? null
    if (
      prevId != null
      && currId !== prevId
      && scriptDirtyRef.current
      && wsId != null
    ) {
      const script = scriptDraftRef.current
      const key = scriptDraftStorageKey(`stream.${wsId}`, prevId)
      writeScriptLocalDraft(key, script)
      void streamingApi.saveDraft(prevId, { script_content: script }).then(() => {
        clearScriptLocalDraft(key)
        setJobs(prev => prev.map(j => (j.id === prevId ? { ...j, script_content: script } : j)))
      }).catch(() => {
        message.warning('上一作业草稿同步失败，已保留本地')
      })
    }
    prevStreamJobIdRef.current = currId

    if (!selected || selected.job_type !== 'SQL') {
      setScriptDirty(false)
      return
    }
    const key = wsId != null ? scriptDraftStorageKey(`stream.${wsId}`, selected.id) : null
    const restored = restoreScriptLocalDraft(key, selected.script_content ?? '')
    if (restored != null) {
      setScriptDraft(restored)
      setScriptDirty(true)
      message.info('已恢复本地未同步草稿，将自动保存到服务端')
    } else {
      setScriptDraft(selected.script_content ?? '')
      setScriptDirty(false)
    }
    setSqlParallelism(selected.parallelism ?? 1)
    const sp = selected.streaming_properties
    if (sp != null && String(sp).trim() !== '') {
      try {
        setStreamingPropsJson(JSON.stringify(JSON.parse(String(sp)), null, 2))
      } catch {
        setStreamingPropsJson(String(sp))
      }
    } else {
      setStreamingPropsJson('{}')
    }
    setOperatorResForm(parseOperatorResForm(sp))
    setResourceTier(parseResourceTier(sp))
  }, [selected?.id, selected?.job_type, wsId])

  const streamDraftKey =
    wsId != null && selected?.job_type === 'SQL'
      ? scriptDraftStorageKey(`stream.${wsId}`, selected.id)
      : null

  const scriptAutosave = useScriptAutosave({
    enabled: Boolean(wsId && selected?.job_type === 'SQL' && !selected.is_locked),
    dirty: scriptDirty,
    value: scriptDraft,
    storageKey: streamDraftKey,
    entityId: selected?.id ?? null,
    persist: async (script, entityId) => {
      const jobId = entityId == null ? null : Number(entityId)
      if (jobId == null || !Number.isFinite(jobId)) throw new Error('no job')
      const updated: any = await streamingApi.saveDraft(jobId, { script_content: script })
      setSelected((prev: any) => (prev && prev.id === jobId
        ? { ...prev, ...updated, script_content: script }
        : prev))
      setJobs(prev => prev.map(j => (j.id === jobId ? { ...j, ...updated, script_content: script } : j)))
    },
    onSynced: (script, entityId) => {
      if (entityId == null) return
      if (selectedIdRef.current !== entityId) return
      if (scriptDraftRef.current !== script) return
      setScriptDirty(false)
    },
    persistKeepalive: (script) => {
      const jobId = selectedIdRef.current
      if (jobId == null || !wsId) return
      const token = localStorage.getItem('token')
      if (!token) return
      const apiOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''
      const url = `${apiOrigin || ''}/api/streaming/jobs/${jobId}?create_history=false`
      try {
        fetch(url, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ script_content: script }),
          keepalive: true,
        }).catch(() => {})
      } catch {
        /* ignore */
      }
    },
  })

  useEffect(() => {
    if (selected?.job_type === 'JAR') {
      const sp = selected.streaming_properties
      if (sp != null && String(sp).trim() !== '') {
        try {
          setJarStreamingPropsJson(JSON.stringify(JSON.parse(String(sp)), null, 2))
        } catch {
          setJarStreamingPropsJson(String(sp))
        }
      } else {
        setJarStreamingPropsJson('{}')
      }
      setOperatorResForm(parseOperatorResForm(sp))
      setResourceTier(parseResourceTier(sp))
    }
  }, [selected?.id, selected?.job_type, selected?.flink_jar_submit_mode, selected?.streaming_properties])

  const handleCreate = async () => {
    const v = await createForm.validateFields()
    const created: any = await streamingApi.createJob({
      workspace_id: wsId,
      name: v.name,
      job_type: v.job_type,
      script_content: v.job_type === 'SQL' ? (v.script_content || '-- Flink SQL\nCREATE TABLE ...') : null,
      parallelism: v.parallelism ?? 1,
    })
    message.success('已创建任务')
    setCreateOpen(false)
    createForm.resetFields()
    await load(false)
    setSelected(created)
  }

  const openRename = () => {
    if (!selected) return
    renameForm.setFieldsValue({ name: selected.name })
    setRenameOpen(true)
  }

  const handleRename = async () => {
    if (!selected) return
    const v = await renameForm.validateFields()
    await streamingApi.updateJob(selected.id, { name: v.name })
    message.success('已重命名')
    setRenameOpen(false)
    await load(true)
  }

  const handleUnlock = async () => {
    if (!selected) return
    try {
      await streamingApi.unlockJob(selected.id)
      message.success('已解锁，可继续编辑与提交')
      await load(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '解锁失败')
    }
  }

  const handleSave = async (): Promise<boolean> => {
    if (!selected) return false
    if (selected.is_locked) {
      message.warning('作业已锁定，请先解锁后再保存')
      return false
    }
    let streaming_properties: string | undefined
    const includeOperatorRes =
      (selected.job_type === 'SQL' && effectiveSqlMode === 'flink_operator')
      || (selected.job_type === 'JAR' && effectiveJarMode === 'flink_operator')
    if (selected.job_type === 'SQL') {
      try {
        streaming_properties = buildStreamingPropertiesJson(streamingPropsJson, operatorResForm, includeOperatorRes, resourceTier)
      } catch {
        message.error('参数调优 JSON 格式无效，请检查')
        return false
      }
    } else if (selected.job_type === 'JAR' && effectiveJarMode === 'flink_operator') {
      try {
        streaming_properties = buildStreamingPropertiesJson(jarStreamingPropsJson, operatorResForm, true, resourceTier)
      } catch {
        message.error('高级配置 JSON 格式无效，请检查')
        return false
      }
    }
    try {
      await streamingApi.updateJob(selected.id, {
        script_content: selected.job_type === 'SQL' ? scriptDraft : undefined,
        main_class: selected.job_type === 'JAR' ? (jarForm.main_class || undefined) : undefined,
        program_args: selected.job_type === 'JAR' ? (jarForm.program_args || undefined) : undefined,
        parallelism: selected.job_type === 'JAR' ? jarForm.parallelism : sqlParallelism,
        ...(selected.job_type === 'SQL' ? { streaming_properties, flink_sql_submit_mode: effectiveSqlMode } : {}),
        ...(selected.job_type === 'JAR' ? { flink_jar_submit_mode: effectiveJarMode, streaming_properties } : {}),
      }, { createHistory: true })
      setScriptDirty(false)
      scriptAutosave.markVersionSaved()
      message.success(selected.job_type === 'SQL' ? '已保存并记入版本历史' : '已保存')
      await load()
      return true
    } catch (e: any) {
      const d = e?.response?.data?.detail
      message.error(typeof d === 'string' ? d : (e?.message || '保存失败'))
      return false
    }
  }

  useEffect(() => {
    if (selected?.job_type === 'JAR') {
      setJarForm({
        main_class: selected.main_class ?? '',
        program_args: selected.program_args ?? '',
        parallelism: selected.parallelism ?? 1,
      })
    }
  }, [selected?.id, selected?.job_type])

  const canPreviewSql = useMemo(
    () => /\bSELECT\b/i.test(scriptDraft || ''),
    [scriptDraft],
  )

  const previewTable = useMemo(() => {
    if (!previewResult?.columns?.length) {
      return { dataSource: [] as ReturnType<typeof rowsToRecordDataSource>, tableColumns: buildQueryTableColumns([]) }
    }
    const colMetas = normalizeQueryColumns(previewResult.columns, previewResult.column_types)
    const dataSource = rowsToRecordDataSource(previewResult.columns, previewResult.rows)
    return {
      dataSource,
      tableColumns: buildQueryTableColumns(colMetas, { dataSource }),
    }
  }, [previewResult])

  const handlePreviewSql = async () => {
    if (!wsId || !selected || selected.job_type !== 'SQL') return
    const sql = (scriptDraft || '').trim()
    if (!sql) {
      message.warning('请先编写 SQL')
      return
    }
    if (!canPreviewSql) {
      message.warning('预览须包含 SELECT 或 WITH…SELECT')
      return
    }
    setPreviewLoading(true)
    setPreviewResult(null)
    try {
      const res: any = await streamingApi.previewSql({ workspace_id: wsId, sql, limit: previewLimit })
      setPreviewResult(res)
      setResultPanelOpen(true)
      if (res?.truncated) message.info(`结果已按上限 ${previewLimit} 行截断`)
    } catch (e: any) {
      const d = e?.response?.data?.detail
      const text = typeof d === 'string' ? d : Array.isArray(d) ? d.map((x: any) => x?.msg || x).join('; ') : '预览失败'
      message.error(text.length > 500 ? `${text.slice(0, 500)}…` : text, 8)
    }
    setPreviewLoading(false)
  }

  const openSubmitDrawer = () => {
    if (!selected) return
    if (selected.is_locked) {
      message.warning('作业已锁定，请先解锁后再提交')
      return
    }
    setSubmitDrawerOpen(true)
  }

  const handleSubmit = async () => {
    if (!selected) return
    if (selected.is_locked) {
      message.warning('作业已锁定，请先解锁后再提交')
      return
    }
    setSubmitDrawerOpen(false)
    if (!canPublishDirect) {
      const saved = await handleSave()
      if (!saved) return
      setApprovalNote('')
      setApprovalOpen(true)
      return
    }
    setSubmitting(true)
    try {
      const saved = await handleSave()
      if (!saved) return
      const res: any = await streamingApi.submitJob(selected.id, selected.job_type === 'SQL' ? scriptDraft : undefined)
      await load()
      if (res?.submit_warning) {
        message.warning(String(res.submit_warning), 10)
      }
      const desc = res?.flink_console_url
        ? (
            <span>
              <a href={res.flink_console_url} target="_blank" rel="noreferrer">打开 Flink Web UI</a>
              <div style={{ marginTop: 6, fontSize: 12, wordBreak: 'break-all' }}>{res.flink_console_url}</div>
              {' · '}失败排查见 <a href={R.stream.monitor}>作业运维</a>
            </span>
          )
        : '未返回控制台链接，请检查后端 FLINK_UI_URL / FLINK_URL。失败原因将写入作业运维中的「启动失败」记录。'
      notification.success({ message: '已提交到 Flink', description: desc, duration: 10 })
    } catch (e: any) {
      const d = e?.response?.data?.detail || '提交失败'
      message.error(typeof d === 'string' ? d : '提交失败')
      await load()
      notification.warning({
        message: '提交失败',
        description: (
          <span>
            详细错误已落库，请在 <a href={R.stream.monitor}>作业运维</a> 中打开「诊断」查看启动阶段日志。
          </span>
        ),
        duration: 8,
      })
    } finally {
      setSubmitting(false)
    }
  }

  const submitPublishApproval = async () => {
    if (!selected || !wsId) return
    try {
      await approvalApi.submit({
        workspace_id: wsId,
        resource_type: 'stream_job',
        resource_id: selected.id,
        action: 'submit_job',
        submit_note: approvalNote || undefined,
      })
      message.success('已提交审批，通过后系统将提交到 Flink')
      setApprovalOpen(false)
      setApprovalNote('')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败')
    }
  }

  const isJobPendingApproval = selected
    ? pendingKeys.has(approvalPendingKey('stream_job', selected.id, 'submit_job'))
    : false

  const handleCancelJob = async () => {
    if (!selected) return
    try {
      const res: any = await streamingApi.cancelJob(selected.id)
      message.success(res?.message || '已停止')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '停止失败')
    }
  }

  const openHistory = async () => {
    if (!selected) return
    try {
      const list: any = await streamingApi.getJobHistory(selected.id)
      setHistoryList(Array.isArray(list) ? list : [])
      setHistoryModal(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载版本历史失败')
    }
  }

  const handleRollbackHistory = async (historyId: number) => {
    if (!selected) return
    try {
      await streamingApi.rollbackJobHistory(selected.id, historyId)
      message.success('已回滚到该版本')
      setHistoryModal(false)
      await load(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '回滚失败')
    }
  }

  const handleDelete = async (row: any) => {
    Modal.confirm({
      title: '删除实时任务？',
      content: row.name,
      onOk: async () => {
        await streamingApi.deleteJob(row.id)
        message.success('已删除')
        if (selected?.id === row.id) setSelected(null)
        await load()
      },
    })
  }

  const handleCopy = async (row: any) => {
    try {
      const created: any = await streamingApi.copyJob(row.id)
      message.success(`已复制为「${created.name}」`)
      await load(false)
      setSelected(created)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '复制失败')
    }
  }

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '提交', dataIndex: 'flink_sql_submit_mode', key: 'sm', width: 92,
      render: (_m: string, row: any) => {
        if (row.job_type === 'JAR' || row.job_type === 'SQL') {
          return <Tag color="purple">Operator</Tag>
        }
        return <Text type="secondary">—</Text>
      },
    },
    {
      title: '类型', dataIndex: 'job_type', key: 'job_type', width: 80,
      render: (t: string) => <Tag color={t === 'SQL' ? 'blue' : 'orange'}>{t}</Tag>,
    },
    {
      title: '操作', key: 'op', width: 88,
      render: (_: any, row: any) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<CopyOutlined />} title="复制作业" onClick={(e) => { e.stopPropagation(); handleCopy(row) }} />
          <Button type="link" danger size="small" icon={<DeleteOutlined />} onClick={(e) => { e.stopPropagation(); handleDelete(row) }} />
        </Space>
      ),
    },
  ]

  const statusColor: Record<string, string> = {
    draft: 'default', running: 'processing', finished: 'success', failed: 'error', cancelled: 'warning',
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>作业开发</Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 12, maxWidth: 900 }}>
        在此维护<strong>草稿与逻辑</strong>：编辑 SQL / JAR、保存、单作业提交试跑；<strong>版本历史</strong>自动记录保存/提交前的上一版逻辑，可回滚。运行列表、Flink 控制台链接、<strong>提交失败与运行时异常</strong>请使用
        {' '}<Link to={R.stream.monitor}>作业运维</Link>（对标阿里云实时计算等产品中「开发与运维」拆分）。
      </Paragraph>

      <Space style={{ marginBottom: 16 }}>
        {sidebarCollapsed && (
          <Tooltip title="显示作业列表">
            <Button icon={<MenuUnfoldOutlined />} onClick={() => setSidebarCollapsedPersist(false)} />
          </Tooltip>
        )}
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { createForm.resetFields(); setCreateOpen(true) }}>
          新建实时作业
        </Button>
        <Button icon={<ReloadOutlined />} onClick={() => load(true)} loading={loading}>刷新</Button>
        <Button icon={<AimOutlined />} onClick={locateSelectedJob} disabled={!selected} title="在左侧列表中定位当前作业">
          定位
        </Button>
      </Space>

      <ResizableSidebar
        storageKey="gido.streamStudio.sidebarWidth"
        defaultWidth={360}
        minWidth={260}
        maxWidth={560}
        collapsed={sidebarCollapsed}
        style={{ minHeight: 560 }}
        left={(
        <div style={{ height: '100%', minHeight: 560, display: 'flex', flexDirection: 'column' }} className="stream-job-list">
          <div style={{ padding: '8px 10px', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: 13 }}>作业列表</span>
            <Tooltip title="隐藏作业列表">
              <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setSidebarCollapsedPersist(true)} />
            </Tooltip>
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <Table
              size="small"
              rowKey="id"
              loading={loading}
              dataSource={jobs}
              columns={columns}
              pagination={false}
              scroll={{ y: 440 }}
              tableLayout="fixed"
              onRow={row => ({
                onClick: () => setSelected(row),
                'data-stream-job-id': String(row.id),
                style: { cursor: 'pointer', background: selected?.id === row.id ? '#e6f4ff' : undefined },
              })}
            />
          </div>
        </div>
        )}
        right={(
        <div style={{ height: '100%', minHeight: 560, minWidth: 0 }}>
          {!selected ? (
            <Card>请从左侧选择作业，或新建 Flink SQL / JAR 任务。</Card>
          ) : (
            <Card
              title={
                <Space wrap>
                  <span>{selected.name}</span>
                  <Button
                    type="link"
                    size="small"
                    icon={<EditOutlined />}
                    disabled={selected.is_locked || (selected.status || '').toLowerCase() === 'running'}
                    onClick={openRename}
                    title={(selected.status || '').toLowerCase() === 'running' ? '运行中的作业不可重命名' : '重命名'}
                  >
                    重命名
                  </Button>
                  <Tag>{selected.job_type}</Tag>
                  {selected.status && (
                    <Tag color={statusColor[selected.status] || 'default'}>{selected.status}</Tag>
                  )}
                  {selected.owner_username && (
                    <Tag>负责人 {selected.owner_username}</Tag>
                  )}
                  {selected.is_locked && <Tag color="orange">已锁定</Tag>}
                </Space>
              }
              extra={
                <Space>
                  {selected.is_locked && (
                    <Button icon={<UnlockOutlined />} onClick={handleUnlock}>解锁</Button>
                  )}
                  <Button
                    icon={<SaveOutlined />}
                    onClick={handleSave}
                    disabled={selected.is_locked}
                    type={selected.job_type === 'SQL' && scriptDirty ? 'default' : 'text'}
                    title={selected.job_type === 'SQL'
                      ? '写入服务端并生成版本历史（自动保存只落草稿、不记版本）'
                      : '保存作业配置'}
                  >
                    {selected.job_type === 'SQL' ? `保存版本${scriptDirty ? ' *' : ''}` : '保存'}
                  </Button>
                  <AutosaveStatusHint
                    visible={selected.job_type === 'SQL' && !selected.is_locked}
                    status={scriptAutosave.status}
                    hint={scriptAutosave.hint}
                  />
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    loading={submitting}
                    onClick={openSubmitDrawer}
                    disabled={selected.is_locked || isJobPendingApproval}
                  >
                    {isJobPendingApproval ? '审批中' : canPublishDirect ? '提交运行' : '提交审批'}
                  </Button>
                  <Popconfirm title="停止该作业？Operator 模式将暂停 FlinkDeployment" onConfirm={handleCancelJob}>
                    <Button danger icon={<StopOutlined />}>停止</Button>
                  </Popconfirm>
                  <Button icon={<ReloadOutlined />} onClick={async () => {
                    try {
                      const s: any = await streamingApi.getStatus(selected.id)
                      const note = s?.note ? ` · ${s.note}` : ''
                      const op = s?.flink_operational?.readiness ? ` · 就绪度 ${s.flink_operational.readiness}` : ''
                      message.info(`状态: ${s.status} / Flink: ${s.flink_status ?? '-'}${op}${note}`)
                      await load()
                    } catch {
                      message.error('同步状态失败')
                    }
                  }}>同步状态</Button>
                  <Button icon={<HistoryOutlined />} onClick={openHistory}>版本历史</Button>
                </Space>
              }
            >
              {selected.last_submit_error && (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="最近一次提交失败（完整内容在「作业运维 → 诊断」）"
                  description={
                    <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0, fontSize: 12, maxHeight: 120, overflow: 'auto' }}>
                      {String(selected.last_submit_error).slice(0, 800)}
                      {(selected.last_submit_error?.length ?? 0) > 800 ? '…' : ''}
                    </pre>
                  }
                />
              )}
              {selected.job_type === 'SQL' && selected.flink_operational?.hints?.length ? (
                <Alert
                  type={selected.flink_operational.readiness === 'blocked' ? 'error' : selected.flink_operational.readiness === 'warning' ? 'warning' : 'info'}
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={`运维就绪度（${selected.flink_operational.readiness}）`}
                  description={(
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                      {selected.flink_operational.hints.map((h: string, i: number) => (
                        <li key={i}>{h}</li>
                      ))}
                    </ul>
                  )}
                />
              ) : null}
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: 12,
                flexWrap: 'wrap',
                padding: '8px 12px',
                marginBottom: 12,
                border: '1px solid #f0f0f0',
                borderRadius: 8,
                background: '#fafafa',
                fontSize: 12,
              }}>
                <Space wrap size={[8, 4]}>
                  <Tag color="purple">Flink Operator</Tag>
                  <span>最近提交：{selected.last_submitted_at ? `${formatInTimeZone(selected.last_submitted_at, displayTz)} · ${selected.last_submitted_by_username || '—'}` : '—'}</span>
                  <span>就绪度：{selected.job_type === 'SQL' && selected.flink_operational?.readiness ? selected.flink_operational.readiness : '—'}</span>
                  {selected.flink_job_id && <span>Job ID：<code>{selected.flink_job_id}</code></span>}
                  {selected.flink_operator_deployment_name && <span>Operator CR：<code>{selected.flink_operator_deployment_name}</code></span>}
                </Space>
                {selected.flink_console_url ? (
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0, height: 'auto' }}
                    onClick={() => openFlinkConsoleUrl(selected.flink_console_url, selected.id)}
                  >
                    打开 Flink UI
                  </Button>
                ) : (
                  <Text type="secondary">提交成功后生成 Flink UI 链接</Text>
                )}
              </div>

              {selected.job_type === 'SQL' ? (
                <>
                  <div style={{ marginBottom: 8, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                    <Button
                      size="small"
                      disabled={selected.is_locked}
                      onClick={() => {
                        setScriptDraft(cdcPaimonSqlTemplate(flinkRuntime?.paimon_warehouse_default || ''))
                        setScriptDirty(true)
                      }}
                    >
                      插入 CDC→Paimon 模板
                    </Button>
                    <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
                  </div>
                  <div style={{ flex: 1, minHeight: 360, display: 'flex', flexDirection: 'column' }}>
                    <div style={{ height: resultPanelOpen ? 520 : 620, border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
                      <Editor
                        height="100%"
                        language="sql"
                        theme={editorAppearance.theme}
                        value={scriptDraft}
                        onChange={selected.is_locked ? undefined : (v => {
                          setScriptDraft(v ?? '')
                          setScriptDirty(true)
                        })}
                        beforeMount={registerDwMonacoThemes}
                        onMount={ed => { editorRef.current = ed }}
                        options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), readOnly: Boolean(selected.is_locked), minimap: { enabled: false } }}
                      />
                    </div>
                    <Collapse
                      style={{ marginTop: 12 }}
                      activeKey={resultPanelOpen ? ['result'] : []}
                      onChange={keys => setResultPanelOpen(Array.isArray(keys) ? keys.includes('result') : keys === 'result')}
                      items={[{
                        key: 'result',
                        label: (
                          <Space>
                            <span>查询结果</span>
                            {previewResult && <Tag>{previewResult.total ?? 0} 行</Tag>}
                          </Space>
                        ),
                        extra: (
                          <Space
                            size={8}
                            onClick={e => e.stopPropagation()}
                            onMouseDown={e => e.stopPropagation()}
                          >
                            <InputNumber
                              min={1}
                              max={10000}
                              size="small"
                              value={previewLimit}
                              onChange={v => setPreviewLimit(Number(v) || 100)}
                              addonBefore="预览行数"
                              style={{ width: 148 }}
                            />
                            <Button
                              size="small"
                              icon={<SearchOutlined />}
                              loading={previewLoading}
                              disabled={!canPreviewSql || selected.is_locked}
                              onClick={handlePreviewSql}
                            >
                              预览查询
                            </Button>
                          </Space>
                        ),
                        children: (
                          <div style={{ height: resultPanelHeight, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                            <div
                              role="separator"
                              aria-orientation="horizontal"
                              title="拖拽调整查询结果高度"
                              onMouseDown={startResultResize}
                              style={{
                                height: 8,
                                flexShrink: 0,
                                cursor: 'row-resize',
                                margin: '-8px 0 0',
                                background: 'linear-gradient(180deg, transparent 0, transparent 3px, #d9d9d9 3px, #d9d9d9 5px, transparent 5px)',
                              }}
                            />
                            {previewResult ? (
                              <QueryResultPanel
                                dataSource={previewTable.dataSource}
                                columns={previewTable.tableColumns}
                                toolbar={(
                                  <div style={{ padding: '8px 12px', fontSize: 12, color: '#666' }}>
                                    共 <strong>{previewResult.total ?? 0}</strong> 行
                                    {previewResult.truncated ? `（已按上限 ${previewLimit} 截断）` : ''}
                                    ；预览在集群内短生命周期 Job 执行，不创建 FlinkDeployment
                                  </div>
                                )}
                              />
                            ) : (
                              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 13, padding: 16, textAlign: 'center' }}>
                                点击「预览查询」在此展示 SELECT 结果（须 SET batch 模式；支持 CREATE TABLE 定义连接器后 SELECT）
                              </div>
                            )}
                          </div>
                        ),
                      }]}
                    />
                  </div>
                </>
              ) : (
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                  <Alert
                    type="info"
                    showIcon
                    message="统一运行时 · Flink Operator + gido-flink-runtime"
                    description="JAR 作业通过 FlinkDeployment Application 提交；制品由 GIDO backend 提供 HTTP 拉取。"
                  />
                  <Tag color="purple">Flink Operator</Tag>
                  {effectiveJarMode === 'flink_operator' && (
                    <>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 8 }}
                      message="Flink Operator 生产"
                      description="生产环境请配置 GIDO_FLINK_OPERATOR_UI_URL_TEMPLATE（Ingress 域名）或 LoadBalancer；本机 Kind 开发设 GIDO_FLINK_OPERATOR_DEV_LOCAL=true 并按提示 port-forward。"
                    />
                    <Alert
                      type="info"
                      showIcon
                      message="Operator 生产提交"
                      description={(
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                          <li>上传 JAR 写入 GIDO 制品库；未配 S3 时 Flink Pod HTTP 拉取，EKS 生产请设 <code>FLINK_OPERATOR_JAR_S3_PREFIX</code>。</li>
                          <li>须填写 <strong>Main Class</strong>；Backend 容器需挂载 kubeconfig。</li>
                          <li>默认 namespace：<code>flink</code>（Kind 集群 <code>kind-gido</code>），Flink 2.0.1 + Operator 1.15。</li>
                        </ul>
                      )}
                    />
                    </>
                  )}
                  <Upload
                    disabled={selected.is_locked}
                    maxCount={1}
                    beforeUpload={async file => {
                      if (!file.name.endsWith('.jar')) {
                        message.error('请上传 .jar')
                        return Upload.LIST_IGNORE
                      }
                      try {
                        await streamingApi.uploadJar(selected.id, file)
                        message.success('JAR 已上传至 Flink')
                        await load()
                      } catch (e: any) {
                        message.error(e?.response?.data?.detail || '上传失败')
                      }
                      return false
                    }}
                    showUploadList={false}
                  >
                    <Button icon={<UploadOutlined />}>上传 JAR{effectiveJarMode === 'flink_operator' ? '（制品库）' : ' 到 Flink'}</Button>
                  </Upload>
                  {effectiveJarMode === 'flink_operator' && (
                    <Collapse
                      ghost
                      style={{ marginBottom: 8 }}
                      items={[
                        {
                          key: 'operator-res',
                          label: 'Operator 资源配置（JM / TM / Slots，留空用平台默认）',
                          children: (
                            <div>
                              <Form.Item label="规格模板" style={{ marginBottom: 12, maxWidth: 360 }}>
                                <Select
                                  allowClear
                                  placeholder="平台默认"
                                  value={resourceTier || undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setResourceTier(v || '')}
                                  options={[
                                    { value: 'small', label: '小 — 轻量作业' },
                                    { value: 'medium', label: '中 — 默认生产' },
                                    { value: 'large', label: '大 — 高资源' },
                                  ]}
                                />
                              </Form.Item>
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
                              <Form.Item label="JM 内存" style={{ marginBottom: 0 }}>
                                <Input placeholder="2048m" value={operatorResForm.jm_memory} disabled={selected.is_locked}
                                  onChange={e => setOperatorResForm(f => ({ ...f, jm_memory: e.target.value }))} />
                              </Form.Item>
                              <Form.Item label="JM CPU" style={{ marginBottom: 0 }}>
                                <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                                  value={operatorResForm.jm_cpu ? Number(operatorResForm.jm_cpu) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, jm_cpu: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              <Form.Item label="TM 内存" style={{ marginBottom: 0 }}>
                                <Input placeholder="4096m" value={operatorResForm.tm_memory} disabled={selected.is_locked}
                                  onChange={e => setOperatorResForm(f => ({ ...f, tm_memory: e.target.value }))} />
                              </Form.Item>
                              <Form.Item label="TM CPU" style={{ marginBottom: 0 }}>
                                <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                                  value={operatorResForm.tm_cpu ? Number(operatorResForm.tm_cpu) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, tm_cpu: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              <Form.Item label="Task Slots" style={{ marginBottom: 0 }}>
                                <InputNumber min={1} style={{ width: '100%' }} placeholder="2"
                                  value={operatorResForm.task_slots ? Number(operatorResForm.task_slots) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, task_slots: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              <Form.Item label="TM 副本数" style={{ marginBottom: 0 }}>
                                <InputNumber min={1} style={{ width: '100%' }} placeholder="自动"
                                  value={operatorResForm.tm_replicas ? Number(operatorResForm.tm_replicas) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, tm_replicas: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              </div>
                            </div>
                          ),
                        },
                        {
                          key: 'advanced',
                          label: '高级 Flink 配置（合并进 flinkConfiguration）',
                          children: (
                            <Input.TextArea
                              rows={6}
                              value={jarStreamingPropsJson}
                              onChange={e => setJarStreamingPropsJson(e.target.value)}
                              disabled={selected.is_locked}
                              style={{ fontFamily: 'monospace', fontSize: 12 }}
                              placeholder={'{\n  "execution.checkpointing.interval": "60s"\n}'}
                            />
                          ),
                        },
                      ]}
                    />
                  )}
                  <Form layout="vertical" style={{ maxWidth: 560 }}>
                    <Form.Item label="入口类 (Main Class)">
                      <Input
                        value={jarForm.main_class}
                        placeholder="com.example.StreamingJob"
                        disabled={selected.is_locked}
                        onChange={e => setJarForm(f => ({ ...f, main_class: e.target.value }))}
                      />
                    </Form.Item>
                    <Form.Item label="运行参数">
                      <Space.Compact style={{ width: '100%' }}>
                        <Input
                          value={jarForm.program_args}
                          placeholder="--key value"
                          disabled={selected.is_locked}
                          onChange={e => setJarForm(f => ({ ...f, program_args: e.target.value }))}
                          style={{ flex: 1 }}
                        />
                        <Tooltip title={selected.is_locked ? '放大查看' : '放大编辑'}>
                          <Button
                            icon={<ExpandAltOutlined />}
                            aria-label="放大运行参数"
                            onClick={() => setProgramArgsExpandOpen(true)}
                          />
                        </Tooltip>
                      </Space.Compact>
                    </Form.Item>
                    <Form.Item label="并行度">
                      <InputNumber
                        min={1}
                        value={jarForm.parallelism}
                        disabled={selected.is_locked}
                        onChange={v => setJarForm(f => ({ ...f, parallelism: Number(v) || 1 }))}
                      />
                    </Form.Item>
                  </Form>
                </Space>
              )}
            </Card>
          )}
        </div>
        )}
      />

      <Modal
        title="运行参数"
        open={programArgsExpandOpen}
        onCancel={() => setProgramArgsExpandOpen(false)}
        onOk={() => setProgramArgsExpandOpen(false)}
        okText="完成"
        cancelButtonProps={{ style: { display: 'none' } }}
        width="min(920px, 92vw)"
        destroyOnClose
      >
        <Input.TextArea
          autoFocus
          rows={18}
          value={jarForm.program_args}
          disabled={selected?.is_locked}
          onChange={e => setJarForm(f => ({ ...f, program_args: e.target.value }))}
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace', fontSize: 13 }}
          placeholder="--key value"
        />
      </Modal>

      <Modal title="新建实时作业" open={createOpen} onOk={handleCreate} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={createForm} layout="vertical" initialValues={{ job_type: 'SQL', parallelism: 1 }}>
          <Form.Item
            name="name"
            label="作业名称"
            rules={[
              { required: true, message: '请输入作业名称' },
              { pattern: STREAM_JOB_NAME_PATTERN, message: STREAM_JOB_NAME_RULE },
            ]}
          >
            <Input placeholder="例如 s3-copy-users" />
          </Form.Item>
          <Paragraph type="secondary" style={{ marginTop: -12, fontSize: 12 }}>{STREAM_JOB_NAME_RULE}</Paragraph>
          <Form.Item name="job_type" label="类型" rules={[{ required: true }]}>
            <Select options={JOB_TYPES} />
          </Form.Item>
          <Form.Item name="parallelism" label="默认并行度">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.job_type !== c.job_type}>
            {({ getFieldValue }) =>
              getFieldValue('job_type') === 'SQL' ? (
                <Form.Item name="script_content" label="初始 SQL（可选）">
                  <Input.TextArea rows={6} placeholder="留空则创建为模板注释" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="重命名作业" open={renameOpen} onOk={handleRename} onCancel={() => setRenameOpen(false)} destroyOnClose>
        <Form form={renameForm} layout="vertical">
          <Form.Item
            name="name"
            label="作业名称"
            rules={[
              { required: true, message: '请输入作业名称' },
              { pattern: STREAM_JOB_NAME_PATTERN, message: STREAM_JOB_NAME_RULE },
            ]}
          >
            <Input placeholder="例如 s3-copy-users" />
          </Form.Item>
          <Alert type="info" showIcon message="命名规范" description={STREAM_JOB_NAME_RULE} />
        </Form>
      </Modal>

      <Drawer
        title={canPublishDirect ? '提交运行配置' : '提交审批配置'}
        placement="right"
        width={520}
        open={submitDrawerOpen}
        onClose={() => setSubmitDrawerOpen(false)}
        destroyOnClose={false}
        extra={<Tag color="purple">Flink Operator</Tag>}
        footer={(
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <Button onClick={() => setSubmitDrawerOpen(false)}>取消</Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={submitting}
              disabled={Boolean(selected?.is_locked) || isJobPendingApproval}
              onClick={handleSubmit}
            >
              {isJobPendingApproval ? '审批中' : canPublishDirect ? '确认提交运行' : '提交审批'}
            </Button>
          </div>
        )}
      >
        {selected?.job_type === 'SQL' ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="FlinkDeployment Application + SQL Runner"
              description={(
                <div style={{ fontSize: 13 }}>
                  <div>Flink {flinkRuntime?.flink_version || '2.0.1'} · 命名空间 {flinkRuntime?.operator_namespace || 'flink'}</div>
                  {flinkRuntime?.paimon_warehouse_default && (
                    <div>默认 Paimon warehouse：<code>{flinkRuntime.paimon_warehouse_default}</code></div>
                  )}
                </div>
              )}
            />
            <Form layout="vertical">
              <Form.Item label="并行度">
                <InputNumber
                  min={1}
                  style={{ width: '100%' }}
                  value={sqlParallelism}
                  onChange={v => setSqlParallelism(Number(v) || 1)}
                  disabled={selected.is_locked}
                />
              </Form.Item>
              <Form.Item label="规格模板">
                <Select
                  allowClear
                  placeholder="平台默认（不套用模板）"
                  value={resourceTier || undefined}
                  disabled={selected.is_locked}
                  onChange={v => setResourceTier(v || '')}
                  options={[
                    { value: 'small', label: '小 — 轻量 SQL / 探查' },
                    { value: 'medium', label: '中 — 默认生产' },
                    { value: 'large', label: '大 — 高并行 / 重 SQL' },
                  ]}
                />
              </Form.Item>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
                <Form.Item label="JM 内存" style={{ marginBottom: 0 }}>
                  <Input placeholder="2048m" value={operatorResForm.jm_memory} disabled={selected.is_locked}
                    onChange={e => setOperatorResForm(f => ({ ...f, jm_memory: e.target.value }))} />
                </Form.Item>
                <Form.Item label="JM CPU" style={{ marginBottom: 0 }}>
                  <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                    value={operatorResForm.jm_cpu ? Number(operatorResForm.jm_cpu) : undefined}
                    disabled={selected.is_locked}
                    onChange={v => setOperatorResForm(f => ({ ...f, jm_cpu: v != null ? String(v) : '' }))} />
                </Form.Item>
                <Form.Item label="TM 内存" style={{ marginBottom: 0 }}>
                  <Input placeholder="4096m" value={operatorResForm.tm_memory} disabled={selected.is_locked}
                    onChange={e => setOperatorResForm(f => ({ ...f, tm_memory: e.target.value }))} />
                </Form.Item>
                <Form.Item label="TM CPU" style={{ marginBottom: 0 }}>
                  <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                    value={operatorResForm.tm_cpu ? Number(operatorResForm.tm_cpu) : undefined}
                    disabled={selected.is_locked}
                    onChange={v => setOperatorResForm(f => ({ ...f, tm_cpu: v != null ? String(v) : '' }))} />
                </Form.Item>
                <Form.Item label="Task Slots" style={{ marginBottom: 0 }}>
                  <InputNumber min={1} style={{ width: '100%' }} placeholder="2"
                    value={operatorResForm.task_slots ? Number(operatorResForm.task_slots) : undefined}
                    disabled={selected.is_locked}
                    onChange={v => setOperatorResForm(f => ({ ...f, task_slots: v != null ? String(v) : '' }))} />
                </Form.Item>
                <Form.Item label="TM 副本数" style={{ marginBottom: 0 }}>
                  <InputNumber min={1} style={{ width: '100%' }} placeholder="自动"
                    value={operatorResForm.tm_replicas ? Number(operatorResForm.tm_replicas) : undefined}
                    disabled={selected.is_locked}
                    onChange={v => setOperatorResForm(f => ({ ...f, tm_replicas: v != null ? String(v) : '' }))} />
                </Form.Item>
              </div>
              <Form.Item label="高级 Flink 配置" style={{ marginTop: 16 }}>
                <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
                  JSON 顶级键会合并进 FlinkDeployment flinkConfiguration；Operator 资源请用上方表单。
                </Paragraph>
                <Input.TextArea
                  rows={8}
                  value={streamingPropsJson}
                  onChange={e => setStreamingPropsJson(e.target.value)}
                  disabled={selected.is_locked}
                  style={{ fontFamily: 'monospace', fontSize: 12 }}
                  placeholder={'{\n  "execution.checkpointing.interval": "60000"\n}'}
                />
              </Form.Item>
            </Form>
            <Divider style={{ margin: '4px 0' }} />
            <div style={{ fontSize: 12, color: 'var(--ant-color-text-secondary)' }}>
              <div>Operator CR：<code>{selected.flink_operator_deployment_name || '提交后生成'}</code></div>
              <div>Flink Job ID：<code>{selected.flink_job_id || '提交后生成'}</code></div>
            </div>
          </Space>
        ) : (
          <Text type="secondary">JAR 作业暂沿用主页面配置。</Text>
        )}
      </Drawer>

      <Modal title="版本历史" open={historyModal} onCancel={() => setHistoryModal(false)} footer={null} width={780} destroyOnClose>
        {historyList.length === 0 && (
          <div style={{ color: '#bbb', textAlign: 'center', padding: 24 }}>
            暂无版本快照。保存时对 SQL / JAR 参数 / 并行度的修改、以及提交运行（SQL 正文变更或 JAR 提交）前，会自动保留上一版内容。
          </div>
        )}
        {historyList.map((h: any) => (
          <div key={h.id} style={{ marginBottom: 12, border: '1px solid #f0f0f0', borderRadius: 4, padding: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <span style={{ color: '#666', fontSize: 12 }}>
                {String(h.saved_at)} · {h.saved_by_username || '—'} · <Tag>{h.job_type}</Tag>
                {h.parallelism != null && <Tag>并行度 {h.parallelism}</Tag>}
                {h.job_type === 'SQL' && (
                  <Tag color={(h.flink_sql_submit_mode || 'flink_operator') === 'flink_operator' ? 'purple' : 'geekblue'}>
                    {(h.flink_sql_submit_mode || 'flink_operator') === 'flink_operator' ? 'Operator' : h.flink_sql_submit_mode === 'kubernetes_application' ? 'Application' : 'Session'}
                  </Tag>
                )}
              </span>
              <Button size="small" onClick={() => handleRollbackHistory(h.id)} disabled={Boolean(selected?.is_locked)}>
                回滚到此版本
              </Button>
            </div>
            {h.job_type === 'SQL' ? (
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 12, maxHeight: 180, overflow: 'auto', margin: 0, whiteSpace: 'pre-wrap' }}>
                {(h.script_content || '').slice(0, 2500)}{(h.script_content?.length ?? 0) > 2500 ? '...' : ''}
              </pre>
            ) : (
              <div style={{ fontSize: 12, color: '#555' }}>
                <div><strong>Main:</strong> {h.main_class || '—'}</div>
                <div><strong>Args:</strong> {h.program_args || '—'}</div>
              </div>
            )}
          </div>
        ))}
      </Modal>

      <PublishApprovalModal
        open={approvalOpen}
        title={`提交发布审批 — ${selected?.name || ''}`}
        hint="普通开发不能直接提交到 Flink 生产集群。审批通过后将使用当前已保存的作业定义提交运行。"
        note={approvalNote}
        onNoteChange={setApprovalNote}
        onCancel={() => { setApprovalOpen(false); setApprovalNote('') }}
        onSubmit={submitPublishApproval}
      />
    </div>
  )
}

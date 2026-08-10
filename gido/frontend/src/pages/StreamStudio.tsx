/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import type { Key } from 'react'
import {
  Button, Space, Tag, message, Modal, Form, Input, InputNumber, Select, Card, Drawer,
  Divider, Typography, Alert, Tooltip,
} from 'antd'
import {
  PlusOutlined, CloudUploadOutlined, SaveOutlined, ReloadOutlined,
  UnlockOutlined, HistoryOutlined, SearchOutlined, EditOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, AimOutlined, ExpandAltOutlined,
} from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { streamingApi, approvalApi } from '../api'
import { useAppStore } from '../store'
import { can, isWorkspaceAdmin, P } from '../perm'
import PublishApprovalModal from '../components/PublishApprovalModal'
import { approvalPendingKey } from '../approvalLabels'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableSidebar from '../components/ResizableSidebar'
import WorkspaceFolderTree, { locateLeafInFolderTree } from '../components/WorkspaceFolderTree'
import QueryResultPanel from '../components/QueryResultPanel'
import EditorResultDock, { EditorResultRowBadge } from '../components/EditorResultDock'
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { R } from '../routes'
import { Link, useNavigate } from 'react-router-dom'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import MonacoFindBar, { bindMonacoFindKeybindings, type MonacoFindBarApi } from '../components/MonacoFindBar'
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
import StreamRuntimeConfig, {
  buildStreamRuntimeProperties,
  EMPTY_OPERATOR_RESOURCES,
  parseStreamRuntimeConfig,
  type OperatorResourceForm,
} from '../components/StreamRuntimeConfig'

const { Paragraph, Text } = Typography
const STREAM_JOB_NAME_RULE = '3-50 位小写字母、数字、短横线，字母开头，字母或数字结尾，例如 s3-copy-users'
const STREAM_JOB_NAME_PATTERN = /^[a-z][a-z0-9-]{1,48}[a-z0-9]$/

const JOB_TYPES = [
  { label: 'Flink SQL', value: 'SQL' },
  { label: 'JAR 作业', value: 'JAR' },
]

type SqlSubmitMode = 'session' | 'kubernetes_application' | 'flink_operator'

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
  const navigate = useNavigate()
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)
  const canWrite = can(user, P.GIDO_STREAM_WRITE, currentWorkspace)
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [jobs, setJobs] = useState<any[]>([])
  const [folders, setFolders] = useState<any[]>([])
  const [treeExpandedKeys, setTreeExpandedKeys] = useState<Key[]>(['root'])
  const [folderModalOpen, setFolderModalOpen] = useState(false)
  const [folderParentId, setFolderParentId] = useState<number | null>(null)
  const [folderName, setFolderName] = useState('')
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<any | null>(null)
  const [scriptDraft, setScriptDraft] = useState('')
  const [scriptDirty, setScriptDirty] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  /** 从目录菜单「新建作业」时带入；顶栏新建则为 null（根级） */
  const [createFolderId, setCreateFolderId] = useState<number | null>(null)
  const [createForm] = Form.useForm()
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameForm] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const editorRef = useRef<any>(null)
  const findApiRef = useRef<MonacoFindBarApi | null>(null)
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())
  const [jarForm, setJarForm] = useState({ main_class: '', program_args: '', parallelism: 1 })
  const [jarArtifacts, setJarArtifacts] = useState<any[]>([])
  const [selectedJarArtifact, setSelectedJarArtifact] = useState<any | null>(null)
  const [connectorVersionOptions, setConnectorVersionOptions] = useState<{ value: number; label: string }[]>([])
  const [fileVersionOptions, setFileVersionOptions] = useState<{ value: number; label: string }[]>([])
  const [programArgsExpandOpen, setProgramArgsExpandOpen] = useState(false)
  const [sqlParallelism, setSqlParallelism] = useState(1)
  /** Flink SQL Gateway Open Session 合并用 JSON（对标阿里云实时计算「参数调优」的轻量版） */
  const [streamingPropsJson, setStreamingPropsJson] = useState('{}')
  const [flinkRuntime, setFlinkRuntime] = useState<any | null>(null)
  /** 终态产品：仅 Flink Operator；提交模式不再在 UI 暴露 */
  const [sqlSubmitMode] = useState<SqlSubmitMode>('flink_operator')
  const [operatorResForm, setOperatorResForm] = useState<OperatorResourceForm>({ ...EMPTY_OPERATOR_RESOURCES })
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
    locateLeafInFolderTree({
      leafId: selected.id,
      leaves: jobs,
      folders,
      expandedKeys: treeExpandedKeys,
      setExpandedKeys: setTreeExpandedKeys,
      treeSelector: '.stream-job-tree',
    })
  }

  const load = useCallback(async (showSpinner = true) => {
    if (!wsId) return
    if (showSpinner) setLoading(true)
    try {
      const [list, folderList, pendingRes]: any = await Promise.all([
        streamingApi.listJobs(wsId),
        streamingApi.listFolders(wsId),
        approvalApi.list(wsId, { status: 'pending', page_size: 200 }),
      ])
      setJobs(list)
      setFolders(folderList || [])
      setTreeExpandedKeys(prev => (prev.length ? prev : ['root']))
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
    if (!wsId) {
      setJarArtifacts([])
      setConnectorVersionOptions([])
      setFileVersionOptions([])
      return
    }
    streamingApi.listJarArtifacts(wsId)
      .then((list: any) => setJarArtifacts(list || []))
      .catch(() => setJarArtifacts([]))

    const loadVersionOpts = async (
      listFn: (ws: number) => Promise<any>,
      getFn: (id: number) => Promise<any>,
      setOpts: (o: { value: number; label: string }[]) => void,
    ) => {
      try {
        const arts: any[] = (await listFn(wsId)) || []
        const details = await Promise.all(arts.map(a => getFn(a.id).catch(() => null)))
        const opts: { value: number; label: string }[] = []
        for (const d of details) {
          if (!d) continue
          for (const v of d.versions || []) {
            if (v.status !== 'active') continue
            opts.push({ value: v.id, label: `${d.name} · v${v.version}` })
          }
        }
        setOpts(opts)
      } catch {
        setOpts([])
      }
    }
    void loadVersionOpts(
      streamingApi.listConnectorArtifacts,
      streamingApi.getConnectorArtifact,
      setConnectorVersionOptions,
    )
    void loadVersionOpts(
      streamingApi.listFileArtifacts,
      streamingApi.getFileArtifact,
      setFileVersionOptions,
    )
  }, [wsId])

  useEffect(() => {
    streamingApi.flinkRuntime().then(setFlinkRuntime).catch(() => setFlinkRuntime(null))
  }, [])

  const effectiveSqlMode: SqlSubmitMode = 'flink_operator'
  const effectiveJarMode = 'flink_operator' as const

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
      setStreamingPropsJson(parseStreamRuntimeConfig(sp).advancedJson)
    } else {
      setStreamingPropsJson('{}')
    }
    const runtimeConfig = parseStreamRuntimeConfig(sp)
    setOperatorResForm(runtimeConfig.operatorResources)
    setResourceTier(runtimeConfig.resourceTier)
  }, [selected?.id, selected?.job_type, wsId])

  const streamDraftKey =
    wsId != null && selected?.job_type === 'SQL'
      ? scriptDraftStorageKey(`stream.${wsId}`, selected.id)
      : null

  const scriptAutosave = useScriptAutosave({
    enabled: Boolean(wsId && canWrite && selected?.job_type === 'SQL' && !selected.is_locked),
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
        setJarStreamingPropsJson(parseStreamRuntimeConfig(sp).advancedJson)
      } else {
        setJarStreamingPropsJson('{}')
      }
      const runtimeConfig = parseStreamRuntimeConfig(sp)
      setOperatorResForm(runtimeConfig.operatorResources)
      setResourceTier(runtimeConfig.resourceTier)
    }
  }, [selected?.id, selected?.job_type, selected?.flink_jar_submit_mode, selected?.streaming_properties])

  const openCreateJob = (folderId: number | null = null) => {
    createForm.resetFields()
    setCreateFolderId(folderId)
    setCreateOpen(true)
  }

  const handleCreate = async () => {
    const v = await createForm.validateFields()
    const created: any = await streamingApi.createJob({
      workspace_id: wsId,
      name: v.name,
      job_type: v.job_type,
      script_content: v.job_type === 'SQL' ? (v.script_content || '-- Flink SQL\nCREATE TABLE ...') : null,
      parallelism: v.parallelism ?? 1,
      folder_id: createFolderId,
    })
    message.success('已创建任务')
    setCreateOpen(false)
    setCreateFolderId(null)
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
        streaming_properties = includeOperatorRes
          ? buildStreamRuntimeProperties(streamingPropsJson, operatorResForm, resourceTier)
          : streamingPropsJson
      } catch {
        message.error('参数调优 JSON 格式无效，请检查')
        return false
      }
    } else if (selected.job_type === 'JAR' && effectiveJarMode === 'flink_operator') {
      try {
        streaming_properties = buildStreamRuntimeProperties(jarStreamingPropsJson, operatorResForm, resourceTier)
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
      if (selected.jar_artifact_id) {
        streamingApi.getJarArtifact(selected.jar_artifact_id)
          .then((detail: any) => setSelectedJarArtifact(detail))
          .catch(() => setSelectedJarArtifact(null))
      } else {
        setSelectedJarArtifact(null)
      }
    }
  }, [selected?.id, selected?.job_type, selected?.jar_artifact_id])

  const bindJarArtifact = async (artifactId?: number) => {
    if (!selected || selected.job_type !== 'JAR') return
    if (artifactId == null) {
      await streamingApi.updateJob(selected.id, {
        jar_artifact_id: null,
        jar_version_id: null,
      }, { createHistory: false })
      setSelectedJarArtifact(null)
      await load(false)
      return
    }
    const detail: any = await streamingApi.getJarArtifact(artifactId)
    if (!detail) {
      message.error('制品不存在或尚未就绪')
      return
    }
    const version = detail.latest_version
      || (detail.versions || []).find((v: any) => v.status === 'active')
      || null
    if (!version) {
      message.warning('该 JAR 尚无可用版本，请先在「资源管理 → JAR 包」上传')
      setSelectedJarArtifact(detail)
      return
    }
    const patch: Record<string, unknown> = {
      jar_artifact_id: artifactId,
      jar_version_id: version?.id ?? null,
    }
    if (!jarForm.main_class.trim() && version?.default_main_class) {
      patch.main_class = version.default_main_class
      setJarForm(f => ({ ...f, main_class: version.default_main_class }))
    }
    await streamingApi.updateJob(selected.id, patch, { createHistory: false })
    setSelectedJarArtifact(detail)
    await load(false)
  }

  const bindJarVersion = async (versionId?: number) => {
    if (!selected || selected.job_type !== 'JAR' || !selectedJarArtifact) return
    const version = selectedJarArtifact.versions?.find((v: any) => v.id === versionId)
    const patch: Record<string, unknown> = { jar_version_id: versionId ?? null }

    if (!jarForm.main_class.trim() && version?.default_main_class) {
      patch.main_class = version.default_main_class
      setJarForm(f => ({ ...f, main_class: version.default_main_class }))
    }
    await streamingApi.updateJob(selected.id, patch, { createHistory: false })
    await load(false)
  }

  const bindConnectorVersions = async (ids: number[]) => {
    if (!selected) return
    await streamingApi.updateJob(selected.id, { connector_version_ids: ids }, { createHistory: false })
    await load(false)
  }

  const bindFileVersions = async (ids: number[]) => {
    if (!selected) return
    await streamingApi.updateJob(selected.id, { dependency_file_version_ids: ids }, { createHistory: false })
    await load(false)
  }

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
    if (!canWrite) {
      message.warning('缺少实时作业写入权限')
      return
    }
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
      await streamingApi.createRelease(selected.id, {
        release_note: '由作业开发提交',
      })
      await load()
      message.success('发布版本已提交，可在作业运维中部署')
    } catch (e: any) {
      const d = e?.response?.data?.detail || '提交发布失败'
      message.error(typeof d === 'string' ? d : '提交发布失败')
      await load()
    } finally {
      setSubmitting(false)
    }
  }

  const submitPublishApproval = async () => {
    if (!selected || !wsId) return
    setSubmitting(true)
    try {
      const saved = await handleSave()
      if (!saved) return
      const release: any = await streamingApi.createRelease(selected.id, {
        release_note: approvalNote || '提交发布审批',
      })
      await approvalApi.submit({
        workspace_id: wsId,
        resource_type: 'stream_job',
        resource_id: selected.id,
        action: 'submit_job',
        submit_note: approvalNote || undefined,
        release_id: release.id,
      })
      message.success('已提交审批，通过后可在作业运维中部署')
      setApprovalOpen(false)
      setApprovalNote('')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const isJobPendingApproval = selected
    ? pendingKeys.has(approvalPendingKey('stream_job', selected.id, 'submit_job'))
    : false

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

  const createFolder = async () => {
    if (!wsId || !folderName.trim()) return
    await streamingApi.createFolder({
      workspace_id: wsId,
      name: folderName.trim(),
      parent_id: folderParentId,
    })
    setFolderModalOpen(false)
    setFolderName('')
    await load(false)
  }

  const statusColor: Record<string, string> = {
    draft: 'default', running: 'processing', finished: 'success', failed: 'error', cancelled: 'warning',
  }
  const selectedJarVersion =
    selectedJarArtifact?.versions?.find((v: any) => v.id === selected?.jar_version_id)
    || (
      selectedJarArtifact != null
      && selected?.jar_version_id != null
      && selectedJarArtifact.latest_version?.id === selected.jar_version_id
        ? selectedJarArtifact.latest_version
        : null
    )
    || selectedJarArtifact?.latest_version
    || null

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>作业开发</Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 12, maxWidth: 900 }}>
        对标实时计算「数据开发」：编写 SQL / JAR、绑定
        {' '}<Link to={R.stream.resources}>资源</Link>、保存版本与提交发布。
        本页只读库加载目录树（与批处理数据开发一致），不轮询集群运行态。
        启停、状态、诊断与 Flink UI 请到
        {' '}<Link to={R.stream.monitor}>作业运维</Link>。
      </Paragraph>

      <Space style={{ marginBottom: 16 }}>
        {sidebarCollapsed && (
          <Tooltip title="显示作业列表">
            <Button icon={<MenuUnfoldOutlined />} onClick={() => setSidebarCollapsedPersist(false)} />
          </Tooltip>
        )}
        <Button type="primary" icon={<PlusOutlined />} disabled={!canWrite} onClick={() => openCreateJob(null)}>
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
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '0 8px' }}>
            <WorkspaceFolderTree
              rootTitle="作业列表"
              treeClassName="stream-job-tree"
              leaves={jobs}
              folders={folders}
              selectedLeafId={selected?.id}
              readOnly={!canWrite}
              expandedKeys={treeExpandedKeys}
              onExpandedKeysChange={setTreeExpandedKeys}
              onSelectLeaf={setSelected}
              onCreateFolder={parentId => {
                setFolderParentId(parentId)
                setFolderName('')
                setFolderModalOpen(true)
              }}
              onRenameFolder={async (id, name) => {
                await streamingApi.renameFolder(id, name)
                await load(false)
              }}
              onDeleteFolder={async id => {
                await streamingApi.deleteFolder(id)
                await load(false)
              }}
              onRenameLeaf={async (id, name) => {
                const job = jobs.find(j => j.id === id)
                if (job?.is_locked) {
                  message.warning('作业已锁定，请先解锁后再重命名')
                  return
                }
                if ((job?.status || '').toLowerCase() === 'running') {
                  message.warning('运行中的作业不可重命名')
                  return
                }
                if (!STREAM_JOB_NAME_PATTERN.test(name)) {
                  message.error(STREAM_JOB_NAME_RULE)
                  return
                }
                await streamingApi.updateJob(id, { name }, { createHistory: false })
                await load(false)
              }}
              onDeleteLeaf={handleDelete}
              onCopyLeaf={handleCopy}
              onMoveAndReorder={async ({ leafId, targetFolderId, orderedLeafIds, folderChanged }) => {
                if (!wsId) return
                if (folderChanged) await streamingApi.moveJobFolder(leafId, targetFolderId)
                await streamingApi.reorderJobs({
                  workspace_id: wsId,
                  folder_id: targetFolderId,
                  job_ids: orderedLeafIds,
                })
                message.success('已移动')
                await load(false)
              }}
              onMoveFolder={async ({ folderId, targetParentId }) => {
                await streamingApi.moveFolderParent(folderId, targetParentId)
                await load(false)
              }}
              folderMenuExtra={f => canWrite ? [
                {
                  key: 'add-job',
                  label: '新建作业',
                  onClick: () => openCreateJob(f.id),
                },
              ] : []}
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
                  {!canWrite && (
                    <Tooltip title="可查看与运行（若有权限），不能修改作业定义">
                      <Tag style={{ margin: 0 }}>只读</Tag>
                    </Tooltip>
                  )}
                  <span>{selected.name}</span>
                  <Button
                    type="link"
                    size="small"
                    icon={<EditOutlined />}
                    disabled={!canWrite || selected.is_locked || (selected.status || '').toLowerCase() === 'running'}
                    onClick={openRename}
                    title={(selected.status || '').toLowerCase() === 'running' ? '运行中的作业不可重命名' : '重命名'}
                  >
                    重命名
                  </Button>
                  <Tag>{selected.job_type}</Tag>
                  {selected.status && (
                    <Tooltip title="库内记录，非实时。运行态请到作业运维查看与同步。">
                      <Tag color={statusColor[selected.status] || 'default'}>{selected.status}</Tag>
                    </Tooltip>
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
                    disabled={!canWrite || selected.is_locked}
                    type={selected.job_type === 'SQL' && scriptAutosave.versionDirty ? 'default' : 'text'}
                    title={selected.job_type === 'SQL'
                      ? '写入服务端并生成版本历史（后台自动落草稿，不记版本、无打扰提示）'
                      : '保存作业配置'}
                  >
                    {selected.job_type === 'SQL'
                      ? `保存版本${scriptAutosave.versionDirty ? ' *' : ''}`
                      : '保存'}
                  </Button>
                  <AutosaveStatusHint
                    visible={selected.job_type === 'SQL' && !selected.is_locked}
                    status={scriptAutosave.status}
                    hint={scriptAutosave.hint}
                  />
                  <Button
                    type="primary"
                    icon={<CloudUploadOutlined />}
                    loading={submitting}
                    onClick={openSubmitDrawer}
                    disabled={!canWrite || selected.is_locked || isJobPendingApproval}
                  >
                    {isJobPendingApproval ? '审批中' : canPublishDirect ? '提交发布' : '提交审批'}
                  </Button>
                  <Tooltip title="启停与运行态在作业运维（对标实时计算运维管理）">
                    <Button onClick={() => navigate(R.stream.monitor)}>作业运维</Button>
                  </Tooltip>
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
                      disabled={!canWrite || selected.is_locked}
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
                    <div style={{ height: resultPanelOpen ? 520 : 620, border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden', position: 'relative' }}>
                      <MonacoFindBar
                        getEditor={() => editorRef.current}
                        apiRef={findApiRef}
                        readOnly={!canWrite || Boolean(selected.is_locked)}
                        theme={editorAppearance.theme}
                      />
                      <Editor
                        height="100%"
                        language="sql"
                        theme={editorAppearance.theme}
                        value={scriptDraft}
                        onChange={!canWrite || selected.is_locked ? undefined : (v => {
                          setScriptDraft(v ?? '')
                          setScriptDirty(true)
                        })}
                        beforeMount={registerDwMonacoThemes}
                        onMount={(ed, monaco) => {
                          editorRef.current = ed
                          bindMonacoFindKeybindings(ed, monaco, () => findApiRef.current)
                        }}
                        options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), readOnly: !canWrite || Boolean(selected.is_locked), minimap: { enabled: false } }}
                      />
                    </div>
                    {resultPanelOpen ? (
                      <div
                        style={{
                          marginTop: 12,
                          height: resultPanelHeight + 40,
                          display: 'flex',
                          flexDirection: 'column',
                          minHeight: 0,
                          border: '1px solid #f0f0f0',
                          borderRadius: 8,
                          overflow: 'hidden',
                        }}
                      >
                        <div
                          role="separator"
                          aria-orientation="horizontal"
                          title="拖拽调整查询结果高度"
                          onMouseDown={startResultResize}
                          style={{
                            height: 8,
                            flexShrink: 0,
                            cursor: 'row-resize',
                            margin: '0 0 -8px',
                            zIndex: 2,
                            background: 'linear-gradient(180deg, transparent 0, transparent 3px, #d9d9d9 3px, #d9d9d9 5px, transparent 5px)',
                          }}
                        />
                        <div style={{ flex: 1, minHeight: 0 }}>
                          <EditorResultDock
                            activeKey="result"
                            onClose={() => setResultPanelOpen(false)}
                            extra={(
                              <Space size={8} style={{ marginRight: 4 }}>
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
                            )}
                            tabs={[{
                              key: 'result',
                              label: (
                                <>
                                  查询结果
                                  {previewResult && <EditorResultRowBadge count={previewResult.total ?? 0} />}
                                </>
                              ),
                              children: (
                                <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
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
                      </div>
                    ) : (
                      <div
                        style={{
                          marginTop: 12,
                          border: '1px solid #f0f0f0',
                          borderRadius: 8,
                          overflow: 'hidden',
                          background: '#fff',
                          display: 'flex',
                          alignItems: 'center',
                          minHeight: 40,
                          padding: '0 12px',
                        }}
                      >
                        <Button type="link" size="small" style={{ padding: '0 14px', fontWeight: 600 }} onClick={() => setResultPanelOpen(true)}>
                          查询结果
                          {previewResult ? <EditorResultRowBadge count={previewResult.total ?? 0} /> : null}
                        </Button>
                        <div style={{ flex: 1 }} />
                        <Space size={8}>
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
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                  <Alert
                    type="info"
                    showIcon
                    message="从「资源管理 · JAR 包」绑定版本"
                    description="JAR 统一在资源管理上传与审计；作业开发仅绑定包及版本，运行参数在提交时配置。"
                  />
                  <Form layout="vertical" style={{ maxWidth: 680 }}>
                    <Form.Item label="JAR 包">
                      <Select
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        value={selected.jar_artifact_id ?? undefined}
                        disabled={!canWrite || selected.is_locked}
                        placeholder="选择 JAR 包"
                        options={jarArtifacts.map(a => ({ value: a.id, label: a.name }))}
                        onChange={v => void bindJarArtifact(v).catch((e: any) => {
                          message.error(e?.response?.data?.detail || '绑定失败')
                        })}
                      />
                    </Form.Item>
                    <Form.Item label="版本">
                      <Select
                        allowClear
                        value={selected.jar_version_id ?? undefined}
                        disabled={!canWrite || selected.is_locked || !selectedJarArtifact}
                        placeholder="选择版本"
                        options={(selectedJarArtifact?.versions || []).map((v: any) => ({
                          value: v.id,
                          label: `v${v.version}${v.status === 'active' ? '' : ` · ${v.status}`}`,
                          disabled: v.status !== 'active',
                        }))}
                        onChange={v => void bindJarVersion(v).catch((e: any) => {
                          message.error(e?.response?.data?.detail || '绑定版本失败')
                        })}
                      />
                    </Form.Item>
                  </Form>
                  {selectedJarVersion ? (
                    <Text type="secondary">
                      上传人 {selectedJarVersion.uploaded_by_username || '—'} ·
                      {' '}{selectedJarVersion.uploaded_at ? formatInTimeZone(selectedJarVersion.uploaded_at, displayTz) : '—'} ·
                      {' '}SHA256 {selectedJarVersion.sha256 ? `${selectedJarVersion.sha256.slice(0, 16)}…` : '—'} ·
                      {' '}{selectedJarVersion.size_bytes != null ? `${Math.round(selectedJarVersion.size_bytes / 1024)} KB` : '—'}
                    </Text>
                  ) : (
                    <Text type="secondary">选择版本后显示上传审计信息。</Text>
                  )}
                  <Link to={R.stream.resourcesJars}>前往资源管理 · JAR 包</Link>
                  <Alert
                    type="info"
                    showIcon
                    message="运行参数与资源配置在「提交发布」抽屉中设置"
                  />
                </Space>
              )}
            </Card>
          )}

          {selected && (
            <Card size="small" title="依赖绑定" style={{ marginTop: 12 }}>
              <Form layout="vertical" style={{ maxWidth: 720 }}>
                <Form.Item
                  label="连接器版本"
                  extra={<Link to={R.stream.resourcesConnectors}>资源管理 · 连接器</Link>}
                >
                  <Select
                    mode="multiple"
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    disabled={!canWrite || selected.is_locked}
                    placeholder="选择连接器版本（部署注入 pipeline.jars）"
                    value={selected.connector_version_ids || []}
                    options={connectorVersionOptions}
                    onChange={v => void bindConnectorVersions(v || []).catch((e: any) => {
                      message.error(e?.response?.data?.detail || '绑定失败')
                    })}
                  />
                </Form.Item>
                <Form.Item
                  label="依赖文件版本"
                  extra={<Link to={R.stream.resourcesFiles}>资源管理 · 依赖文件</Link>}
                >
                  <Select
                    mode="multiple"
                    allowClear
                    showSearch
                    optionFilterProp="label"
                    disabled={!canWrite || selected.is_locked}
                    placeholder="选择依赖文件版本（本轮仅落库绑定）"
                    value={selected.dependency_file_version_ids || []}
                    options={fileVersionOptions}
                    onChange={v => void bindFileVersions(v || []).catch((e: any) => {
                      message.error(e?.response?.data?.detail || '绑定失败')
                    })}
                  />
                </Form.Item>
              </Form>
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

      <Modal
        title="新建实时作业"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => { setCreateOpen(false); setCreateFolderId(null) }}
        destroyOnClose
      >
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
          {createFolderId != null && (
            <Paragraph type="secondary" style={{ marginTop: -8, fontSize: 12 }}>
              将创建到目录：{folders.find(f => f.id === createFolderId)?.name || createFolderId}
            </Paragraph>
          )}
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

      <Modal
        title="新建目录"
        open={folderModalOpen}
        onOk={() => void createFolder().catch((e: any) => message.error(e?.response?.data?.detail || '创建目录失败'))}
        onCancel={() => setFolderModalOpen(false)}
        okButtonProps={{ disabled: !folderName.trim() }}
        destroyOnClose
      >
        <Form layout="vertical">
          <Form.Item label="目录名称" required>
            <Input
              autoFocus
              value={folderName}
              onChange={e => setFolderName(e.target.value)}
              onPressEnter={() => void createFolder()}
              placeholder="请输入目录名称"
            />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={canPublishDirect ? '提交发布配置' : '提交审批配置'}
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
              icon={<CloudUploadOutlined />}
              loading={submitting}
              disabled={!canWrite || Boolean(selected?.is_locked) || isJobPendingApproval}
              onClick={handleSubmit}
            >
              {isJobPendingApproval ? '审批中' : canPublishDirect ? '确认提交发布' : '提交审批'}
            </Button>
          </div>
        )}
      >
        {selected ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message={selected.job_type === 'SQL'
                ? 'FlinkDeployment Application + SQL Runner'
                : 'FlinkDeployment Application + JAR'}
              description={(
                <div style={{ fontSize: 13 }}>
                  <div>Flink {flinkRuntime?.flink_version || '2.0.1'} · 命名空间 {flinkRuntime?.operator_namespace || 'flink'}</div>
                  {selected.job_type === 'SQL' && flinkRuntime?.paimon_warehouse_default && (
                    <div>默认 Paimon warehouse：<code>{flinkRuntime.paimon_warehouse_default}</code></div>
                  )}
                </div>
              )}
            />
            <Form layout="vertical">
              {selected.job_type === 'JAR' && (
                <>
                  <Form.Item label="入口类 (Main Class)">
                    <Input
                      value={jarForm.main_class}
                      placeholder="com.example.StreamingJob"
                      disabled={!canWrite || selected.is_locked}
                      onChange={e => setJarForm(f => ({ ...f, main_class: e.target.value }))}
                    />
                  </Form.Item>
                  <Form.Item label="运行参数">
                    <Space.Compact style={{ width: '100%' }}>
                      <Input
                        value={jarForm.program_args}
                        placeholder="--key value"
                        disabled={!canWrite || selected.is_locked}
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
                </>
              )}
              <Form.Item label="并行度">
                <InputNumber
                  min={1}
                  style={{ width: '100%' }}
                  value={selected.job_type === 'SQL' ? sqlParallelism : jarForm.parallelism}
                  onChange={v => {
                    const parallelism = Number(v) || 1
                    if (selected.job_type === 'SQL') setSqlParallelism(parallelism)
                    else setJarForm(f => ({ ...f, parallelism }))
                  }}
                  disabled={!canWrite || selected.is_locked}
                />
              </Form.Item>
              <StreamRuntimeConfig
                resourceTier={resourceTier}
                onResourceTierChange={setResourceTier}
                operatorResources={operatorResForm}
                onOperatorResourcesChange={setOperatorResForm}
                advancedJson={selected.job_type === 'SQL' ? streamingPropsJson : jarStreamingPropsJson}
                onAdvancedJsonChange={value => {
                  if (selected.job_type === 'SQL') setStreamingPropsJson(value)
                  else setJarStreamingPropsJson(value)
                }}
                disabled={!canWrite || selected.is_locked}
              />
            </Form>
            <Divider style={{ margin: '4px 0' }} />
            <div style={{ fontSize: 12, color: 'var(--ant-color-text-secondary)' }}>
              <div>Operator CR：<code>{selected.flink_operator_deployment_name || '提交后生成'}</code></div>
              <div>Flink Job ID：<code>{selected.flink_job_id || '提交后生成'}</code></div>
            </div>
          </Space>
        ) : (
          <Text type="secondary">请先选择作业。</Text>
        )}
      </Drawer>

      <Modal title="版本历史" open={historyModal} onCancel={() => setHistoryModal(false)} footer={null} width={780} destroyOnClose>
        {historyList.length === 0 && (
          <div style={{ color: '#bbb', textAlign: 'center', padding: 24 }}>
            暂无版本快照。显式保存版本会记录 SQL / JAR 参数与并行度，后台草稿自动保存不写版本历史。
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
        hint="提交的是当前已保存的发布定义，不会从作业开发直接部署。审批通过后由具备运行权限的人员在作业运维中部署。"
        note={approvalNote}
        onNoteChange={setApprovalNote}
        onCancel={() => { setApprovalOpen(false); setApprovalNote('') }}
        onSubmit={submitPublishApproval}
      />
    </div>
  )
}

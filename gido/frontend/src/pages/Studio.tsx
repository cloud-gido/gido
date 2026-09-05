/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, useCallback, useMemo, type Key, type PointerEvent } from 'react'
import {
  Button, Input, Select, Tag, message, Spin, Tooltip,
  Modal, Form, Tabs, Space, Badge, Table, DatePicker
} from 'antd'
import {
  PlayCircleOutlined, SaveOutlined, CloudUploadOutlined, PlusOutlined,
  DeleteOutlined, FileOutlined, FolderAddOutlined,
  LoadingOutlined, CheckCircleOutlined,
  ReloadOutlined, SettingOutlined, FormatPainterOutlined, UnlockOutlined,
  LockOutlined, DownloadOutlined, MenuFoldOutlined, AimOutlined,
  ExclamationCircleOutlined, TableOutlined, DiffOutlined, ScheduleOutlined,
} from '@ant-design/icons'
import Editor, { DiffEditor } from '@monaco-editor/react'
import { format as sqlFormat } from 'sql-formatter'
import type { Dayjs } from 'dayjs'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { studioApi, datasourceApi, approvalApi, workflowApi } from '../api'
import { BRAND } from '../branding'
import { R } from '../routes'
import { useAppStore } from '../store'
import { can, isWorkspaceAdmin, P } from '../perm'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableVerticalSplit from '../components/ResizableVerticalSplit'
import StudioWorkbenchShell, {
  StudioWorkbenchActiveEntityTitle,
  StudioWorkbenchEmpty,
  StudioWorkbenchExpandSidebarButton,
  StudioWorkbenchStage,
  StudioWorkbenchToolbar,
  StudioWorkbenchTopStrip,
} from '../components/StudioWorkbenchShell'
import AutosaveStatusHint from '../components/AutosaveStatusHint'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import MonacoFindBar, { bindMonacoFindKeybindings, type MonacoFindBarApi } from '../components/MonacoFindBar'
import { bindMonacoScriptKeybindings } from '../utils/monacoScriptKeybindings'
import { useSqlSchemaCompletion } from '../hooks/useSqlSchemaCompletion'
import SqlSchemaBrowserDrawer from '../components/SqlSchemaBrowserDrawer'
import {
  cancelScheduledEditorSessionWrite,
  canPersistEditorSession,
  isEditorTabContentPending,
  normalizeEditorSession,
  readEditorSession,
  readLegacyStudioLastNodeId,
  scheduleWriteEditorSession,
  writeEditorSession,
} from '../utils/editorSessionStore'
import {
  canRunStudioTabShortcut,
  mergeStudioSessionTabOrder,
  planStudioSessionTabOrder,
} from '../utils/studioTabChrome'
import StudioEditorTabStrip from '../components/StudioEditorTabStrip'
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { buildDefaultSqlPublishScript } from '../utils/sqlPublishTemplate'
import {
  datasourceTagText,
  resolveDatasourceForRun,
} from '../utils/workspaceDatasource'
import QueryResultPanel from '../components/QueryResultPanel'
import EditorResultDock, { EditorResultRowBadge } from '../components/EditorResultDock'
import { exportRowsToCsv } from '../utils/csvExport'
import { SQL_RESULT_ROW_CAP } from '../utils/sqlResultRowLimit'
import { pruneWidths, resolveResultColumnOrder } from '../utils/resultTableMeta'
import NodeConfigModal from '../components/NodeConfigModal'
import { useScriptAutosave } from '../hooks/useScriptAutosave'
import { sortLeavesByOrderThenName } from '../utils/treeSort'
import WorkspaceFolderTree, { locateLeafInFolderTree } from '../components/WorkspaceFolderTree'
import {
  clearScriptLocalDraft,
  restoreScriptLocalDraft,
  scriptDraftStorageKey,
  writeScriptLocalDraft,
} from '../utils/scriptLocalDraft'

const NODE_TYPES = ['SQL', 'PYTHON', 'SHELL', 'SYNC', 'VIRTUAL', 'DEPENDENT']
const LANG_MAP: Record<string, string> = { SQL: 'sql', PYTHON: 'python', SHELL: 'shell', SYNC: 'json', DEPENDENT: 'plaintext' }
const TYPE_COLOR: Record<string, string> = {
  SQL: 'blue', PYTHON: 'green', SHELL: 'orange', SYNC: 'purple', VIRTUAL: 'default', DEPENDENT: 'magenta',
}

const STUDIO_RESULT_COL_META = 'gido.studio.resultTableMeta.v1'

type StudioResultColMeta = {
  order: string[]
  widths: Record<string, number>
  /** 产生 order 时的结果列序；与本次结果不一致则展示跟 SQL */
  sourceKeys?: string[]
}

function loadStudioResultMetaMap(): Record<string, StudioResultColMeta> {
  try {
    const raw = sessionStorage.getItem(STUDIO_RESULT_COL_META)
    if (!raw) return {}
    const o = JSON.parse(raw) as Record<string, StudioResultColMeta>
    return o && typeof o === 'object' ? o : {}
  } catch {
    return {}
  }
}

function saveStudioResultMetaNode(nodeId: number, meta: StudioResultColMeta) {
  try {
    const all = loadStudioResultMetaMap()
    all[String(nodeId)] = meta
    sessionStorage.setItem(STUDIO_RESULT_COL_META, JSON.stringify(all))
  } catch {
    /* ignore */
  }
}

function sortNodesList(list: any[]): any[] {
  return sortLeavesByOrderThenName(list)
}

export default function StudioPage() {
  const { currentWorkspace, pendingOpenNodeId, setPendingOpenNodeId, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)
  const canWrite = can(user, P.GIDO_BATCH_STUDIO_WRITE, currentWorkspace)
  const canRun = can(user, P.GIDO_BATCH_STUDIO_RUN, currentWorkspace)

  // 节点列表
  const [nodes, setNodes] = useState<any[]>([])
  const [folders, setFolders] = useState<any[]>([])
  const [datasources, setDatasources] = useState<any[]>([])

  // 编辑器 ref（用于格式化 / 自研查找）
  const editorRef = useRef<any>(null)
  const findApiRef = useRef<MonacoFindBarApi | null>(null)
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())
  const [openTabs, setOpenTabs] = useState<any[]>([])
  const [activeTabId, setActiveTabId] = useState<number | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gido.studio.sidebarCollapsed') === '1'
    } catch {
      return false
    }
  })
  const [treeExpandedKeys, setTreeExpandedKeys] = useState<Key[]>(['root'])

  // 编辑器内容（按 nodeId 存储，相对服务端尚未确认的修改）
  const [dirtyMap, setDirtyMap] = useState<Record<number, string>>({})

  // 运行状态
  const [runningId, setRunningId] = useState<number | null>(null)
  const runningIdRef = useRef(runningId)
  runningIdRef.current = runningId
  const [logMap, setLogMap] = useState<Record<number, string>>({})
  const [resultMap, setResultMap] = useState<Record<number, { columns: string[], rows: any[][], total: number } | null>>({})
  const [logPanelOpen, setLogPanelOpen] = useState(false)
  const [resultTab, setResultTab] = useState<Record<number, 'log' | 'result'>>({})  // 每个节点底部面板激活的 tab
  /** 查询结果表：列顺序与列宽（按节点，写入 sessionStorage） */
  const [resultColMeta, setResultColMeta] = useState<StudioResultColMeta>({ order: [], widths: {} })

  // 新建节点弹窗
  const [createModal, setCreateModal] = useState(false)
  const [createForm] = Form.useForm()
  const [createFolderId, setCreateFolderId] = useState<number | null>(null)

  // 新建文件夹弹窗
  const [folderModal, setFolderModal] = useState(false)
  const [folderForm] = Form.useForm()
  const [folderParentId, setFolderParentId] = useState<number | null>(null)

  // 节点配置弹窗（与工作流 DAG 共用 NodeConfigModal）
  const [configModal, setConfigModal] = useState(false)
  const [historyModal, setHistoryModal] = useState(false)
  const [historyList, setHistoryList] = useState<any[]>([])
  const [diffHistory, setDiffHistory] = useState<{ saved_at?: string; script_content?: string } | null>(null)
  const [schemaBrowserOpen, setSchemaBrowserOpen] = useState(false)
  const [runBizdate, setRunBizdate] = useState<Dayjs | null>(null)
  const [workflows, setWorkflows] = useState<any[]>([])
  /** 当前用户是否持有各节点的协作编辑锁（与发布锁定 is_locked 独立） */
  const [editLockHeld, setEditLockHeld] = useState<Record<number, boolean>>({})
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set())
  const [approvalNote, setApprovalNote] = useState('')
  const [approvalModalOpen, setApprovalModalOpen] = useState(false)

  const load = async () => {
    if (!wsId) return
    const [n, d, f, pendingRes, wfs]: any = await Promise.all([
      studioApi.listNodes(wsId),
      datasourceApi.list(wsId),
      studioApi.listFolders(wsId),
      approvalApi.list(wsId, { status: 'pending', page_size: 200 }),
      workflowApi.listAll(wsId).catch(() => ({ items: [] })),
    ])
    setNodes(sortNodesList(n as unknown as any[]))
    setDatasources(d as unknown as any[])
    setFolders(f as unknown as any[])
    setWorkflows(Array.isArray(wfs?.items) ? wfs.items : (Array.isArray(wfs) ? wfs : []))
    setPendingKeys(
      new Set((pendingRes?.items || []).map((i: any) => `${i.resource_type}:${i.resource_id}:${i.action}`)),
    )
  }

  useEffect(() => { load() }, [wsId])

  const setSidebarCollapsedPersist = (collapsed: boolean) => {
    setSidebarCollapsed(collapsed)
    try {
      localStorage.setItem('gido.studio.sidebarCollapsed', collapsed ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  /** 展开目录并滚动到当前脚本在树中的位置 */
  const locateActiveInTree = () => {
    const node = openTabs.find(t => t.id === activeTabId)
    if (!node) {
      message.info('请先打开一个脚本')
      return
    }
    setSidebarCollapsedPersist(false)
    locateLeafInFolderTree({
      leafId: node.id,
      leaves: nodes,
      folders,
      expandedKeys: treeExpandedKeys,
      setExpandedKeys: setTreeExpandedKeys,
      treeSelector: '.studio-node-tree',
    })
  }

  const openTabsRef = useRef(openTabs)
  openTabsRef.current = openTabs
  const activeTabIdRef = useRef(activeTabId)
  activeTabIdRef.current = activeTabId
  const tabHydrateInflightRef = useRef(new Set<number>())
  const [tabContentLoading, setTabContentLoading] = useState<Record<number, boolean>>({})
  const tabContentLoadingRef = useRef(tabContentLoading)
  tabContentLoadingRef.current = tabContentLoading
  const [tabContentError, setTabContentError] = useState<Record<number, string>>({})
  const tabContentErrorRef = useRef(tabContentError)
  tabContentErrorRef.current = tabContentError

  const applyLocalDraftIfAny = useCallback((full: any, activate: boolean) => {
    if (wsId == null || !canWrite || full.is_locked) return
    const key = scriptDraftStorageKey(`studio.${wsId}`, full.id)
    const restored = restoreScriptLocalDraft(key, full.script_content ?? '')
    if (restored != null) {
      setDirtyMap(prev => (prev[full.id] !== undefined ? prev : { ...prev, [full.id]: restored }))
      if (activate) {
        message.info('已恢复本地未同步草稿，持有编辑锁后将自动保存到服务端')
      }
    }
  }, [wsId, canWrite])

  /** 按需拉脚本正文；失败可 force 重试（再点 Tab / 右键「重新加载」） */
  const ensureTabContent = useCallback(async (nodeId: number, opts?: { activate?: boolean; force?: boolean }) => {
    const activate = opts?.activate === true
    const force = opts?.force === true
    const tab = openTabsRef.current.find(t => t.id === nodeId)
    if (!tab) return
    const pending = isEditorTabContentPending(tab)
    if (!pending && !force) return
    if (tabHydrateInflightRef.current.has(nodeId)) return
    tabHydrateInflightRef.current.add(nodeId)
    setTabContentLoading(prev => ({ ...prev, [nodeId]: true }))
    setTabContentError(prev => {
      if (prev[nodeId] === undefined) return prev
      const next = { ...prev }
      delete next[nodeId]
      return next
    })
    try {
      const full: any = await studioApi.getNode(nodeId)
      setNodes(prev => prev.map(n => (n.id === full.id ? { ...n, ...full } : n)))
      setOpenTabs(prev => prev.map(t => (t.id === full.id ? { ...t, ...full } : t)))
      applyLocalDraftIfAny(full, activate || activeTabIdRef.current === nodeId)
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || '加载脚本失败'
      setTabContentError(prev => ({ ...prev, [nodeId]: String(detail) }))
      if (activate || activeTabIdRef.current === nodeId) {
        message.error(detail)
      }
    } finally {
      tabHydrateInflightRef.current.delete(nodeId)
      setTabContentLoading(prev => {
        if (!prev[nodeId]) return prev
        const next = { ...prev }
        delete next[nodeId]
        return next
      })
    }
  }, [applyLocalDraftIfAny])

  const activateTab = useCallback((nodeId: number) => {
    setActiveTabId(nodeId)
    setLogPanelOpen(false)
    const tab = openTabsRef.current.find(t => t.id === nodeId)
    const hadError = tabContentErrorRef.current[nodeId] != null
    // 同 Tab 再点：失败或未加载时拉取/重试（effect 不会因同 id 再触发）
    if (tab && (isEditorTabContentPending(tab) || hadError)) {
      void ensureTabContent(nodeId, { activate: true, force: hadError })
    }
  }, [ensureTabContent])

  const openNode = useCallback(async (node: any, opts?: { activate?: boolean }) => {
    if (!node?.id) return
    const activate = opts?.activate !== false
    if (openTabsRef.current.some(t => t.id === node.id)) {
      if (activate) activateTab(node.id)
      return
    }

    let full = node
    // 列表接口默认不带 script_content，打开时再拉详情
    if (isEditorTabContentPending(node)) {
      try {
        full = await studioApi.getNode(node.id) as any
      } catch (e: any) {
        if (activate) {
          message.error(e?.response?.data?.detail || e?.message || '加载脚本失败')
        }
        return
      }
      setNodes(prev => prev.map(n => (n.id === full.id ? { ...n, ...full } : n)))
    }

    setOpenTabs(prev => (prev.find(t => t.id === full.id) ? prev : [...prev, full]))
    if (activate) {
      setActiveTabId(full.id)
      setLogPanelOpen(false)
    }
    applyLocalDraftIfAny(full, activate)
  }, [applyLocalDraftIfAny, activateTab])

  const prevWsIdRef = useRef<number | undefined>(undefined)
  const studioRestoreDoneRef = useRef(false)
  /** 会话 Tab 壳挂载完成前禁止 persist，避免空 tabs 冲掉 localStorage */
  const studioSessionHydratedRef = useRef(false)
  useEffect(() => {
    if (prevWsIdRef.current !== undefined && prevWsIdRef.current !== wsId) {
      if (prevWsIdRef.current != null && studioSessionHydratedRef.current) {
        cancelScheduledEditorSessionWrite('studio', prevWsIdRef.current)
        writeEditorSession('studio', prevWsIdRef.current, {
          tabIds: openTabsRef.current.map(t => t.id),
          activeId: activeTabIdRef.current,
        })
      }
      setOpenTabs([])
      setActiveTabId(null)
      setDirtyMap({})
      setEditLockHeld({})
      setLogPanelOpen(false)
      setRunningId(null)
      setTabContentLoading({})
      setTabContentError({})
      tabHydrateInflightRef.current.clear()
      studioRestoreDoneRef.current = false
      studioSessionHydratedRef.current = false
      if (wsId != null) cancelScheduledEditorSessionWrite('studio', wsId)
    }
    prevWsIdRef.current = wsId
  }, [wsId])

  // URL ?node_id= / pendingOpen / 多 Tab 会话：一次挂上全部 Tab 壳，仅 active 拉正文
  useEffect(() => {
    if (!wsId || nodes.length === 0) return
    if (studioRestoreDoneRef.current) return

    cancelScheduledEditorSessionWrite('studio', wsId)

    const existing = new Set(nodes.map((n: any) => n.id as number))
    const urlNodeRaw = searchParams.get('node_id')
    const urlNodeId = urlNodeRaw != null ? Number(urlNodeRaw) : null

    const finishUrlParam = () => {
      if (urlNodeRaw != null) {
        const next = new URLSearchParams(searchParams)
        next.delete('node_id')
        setSearchParams(next, { replace: true })
      }
    }

    const finishPersistReady = () => {
      studioSessionHydratedRef.current = true
      scheduleWriteEditorSession('studio', wsId, {
        tabIds: openTabsRef.current.map(t => t.id),
        activeId: activeTabIdRef.current,
      })
    }

    /** 视觉一次对齐：用 slim list 挂 Tab；正文交给 ensureTabContent */
    const seedSessionTabs = (tabIds: number[], activeId: number | null) => {
      const normalized = normalizeEditorSession(tabIds, activeId, { existingIds: existing })
      if (!normalized.tabIds.length) return
      const byId = new Map(nodes.map((n: any) => [n.id as number, n]))
      const currentById = new Map(openTabsRef.current.map((n: any) => [n.id as number, n]))
      const stubs = normalized.tabIds
        // 用户抢先打开的完整节点优先于 slim list 壳，避免恢复时冲掉已加载正文。
        .map(id => currentById.get(id) ?? byId.get(id))
        .filter((n): n is any => Boolean(n))
      if (!stubs.length) return
      // 同 tick 内 finishPersistReady 会读 ref；先同步再 setState
      openTabsRef.current = stubs
      activeTabIdRef.current = normalized.activeId
      setOpenTabs(stubs)
      setActiveTabId(normalized.activeId)
      setLogPanelOpen(false)
    }

    const stored = readEditorSession('studio', wsId)
    let sessionTabIds = stored?.tabIds ?? []
    let sessionActiveId = stored?.activeId ?? null
    if (!sessionTabIds.length) {
      const legacy = readLegacyStudioLastNodeId(wsId)
      if (legacy != null) {
        sessionTabIds = [legacy]
        sessionActiveId = legacy
      }
    }

    if (pendingOpenNodeId != null || (urlNodeId != null && Number.isFinite(urlNodeId))) {
      const preferId = pendingOpenNodeId ?? urlNodeId!
      setPendingOpenNodeId(null)
      finishUrlParam()
      studioRestoreDoneRef.current = true
      const planned = planStudioSessionTabOrder(sessionTabIds, preferId)
      seedSessionTabs(planned.tabIds, planned.activeId)
      finishPersistReady()
      return
    }

    if (openTabs.length > 0) {
      studioRestoreDoneRef.current = true
      const planned = mergeStudioSessionTabOrder(
        sessionTabIds,
        openTabsRef.current.map(t => t.id),
        activeTabIdRef.current,
      )
      seedSessionTabs(planned.tabIds, planned.activeId)
      finishPersistReady()
      return
    }

    studioRestoreDoneRef.current = true
    seedSessionTabs(sessionTabIds, sessionActiveId)
    finishPersistReady()
  }, [
    wsId,
    nodes,
    pendingOpenNodeId,
    openTabs.length,
    setPendingOpenNodeId,
    searchParams,
    setSearchParams,
  ])

  // 激活 Tab 时再拉脚本（会话后台 Tab / 切 Tab）
  useEffect(() => {
    if (activeTabId == null) return
    void ensureTabContent(activeTabId, { activate: true })
  }, [activeTabId, ensureTabContent])

  useEffect(() => {
    if (wsId == null) return
    if (!canPersistEditorSession({ hydrated: studioSessionHydratedRef.current })) return
    scheduleWriteEditorSession('studio', wsId, {
      tabIds: openTabs.map(t => t.id),
      activeId: activeTabId,
    })
  }, [wsId, openTabs, activeTabId])

  // 离开数据开发页时同步落盘，避免仅防抖未触发就丢多 Tab
  useEffect(() => {
    if (wsId == null) return
    return () => {
      cancelScheduledEditorSessionWrite('studio', wsId)
      if (!studioSessionHydratedRef.current) return
      writeEditorSession('studio', wsId, {
        tabIds: openTabsRef.current.map(t => t.id),
        activeId: activeTabIdRef.current,
      })
    }
  }, [wsId])

  /** 与 editLockHeld 同步，供 effect / 事件里读取最新占锁状态 */
  const editLockHeldRef = useRef(editLockHeld)
  editLockHeldRef.current = editLockHeld
  const dirtyMapRef = useRef(dirtyMap)
  dirtyMapRef.current = dirtyMap
  const flushingRef = useRef<Set<number>>(new Set())

  /** 多 Tab：将指定节点脏内容静默写库（共用 saveDraft API） */
  const flushDraftToServer = useCallback(async (nodeId: number): Promise<boolean> => {
    if (!wsId) return false
    const script = dirtyMapRef.current[nodeId]
    if (script === undefined) return true
    if (editLockHeldRef.current[nodeId] !== true) return false
    if (flushingRef.current.has(nodeId)) {
      const deadline = Date.now() + 2500
      while (flushingRef.current.has(nodeId) && Date.now() < deadline) {
        await new Promise(r => window.setTimeout(r, 40))
      }
      if (flushingRef.current.has(nodeId)) return false
      // 并发冲刷已结束：若 dirty 已清则视为成功
      if (dirtyMapRef.current[nodeId] === undefined) return true
    }
    const tab = openTabsRef.current.find(t => t.id === nodeId)
    if (!tab || tab.is_locked) return false
    flushingRef.current.add(nodeId)
    try {
      const latest = dirtyMapRef.current[nodeId]
      if (latest === undefined) return true
      const updated: any = await studioApi.saveDraft(nodeId, {
        workspace_id: wsId,
        name: tab.name,
        node_type: tab.node_type,
        script_content: latest,
      })
      if (dirtyMapRef.current[nodeId] !== latest) return true
      setNodes(prev => prev.map(n => (n.id === nodeId ? { ...n, ...updated, script_content: latest } : n)))
      setOpenTabs(prev => prev.map(t => (t.id === nodeId ? { ...t, ...updated, script_content: latest } : t)))
      setDirtyMap(prev => {
        const n = { ...prev }
        delete n[nodeId]
        return n
      })
      clearScriptLocalDraft(scriptDraftStorageKey(`studio.${wsId}`, nodeId))
      return true
    } catch {
      return false
    } finally {
      flushingRef.current.delete(nodeId)
    }
  }, [wsId])

  const flushDraftKeepalive = useCallback((nodeId: number) => {
    if (!wsId) return
    const script = dirtyMapRef.current[nodeId]
    if (script === undefined) return
    if (editLockHeldRef.current[nodeId] !== true) return
    const token = localStorage.getItem('token')
    if (!token) return
    const tab = openTabsRef.current.find(t => t.id === nodeId)
    const apiOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''
    const url = `${apiOrigin || ''}/api/studio/nodes/${nodeId}?create_history=false`
    try {
      fetch(url, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          workspace_id: wsId,
          name: tab?.name || 'node',
          node_type: tab?.node_type || 'SQL',
          script_content: script,
        }),
        keepalive: true,
      }).catch(() => {})
    } catch {
      /* ignore */
    }
  }, [wsId])

  /** 切换 Tab 时先冲刷草稿；成功才释放上一节点编辑锁 */
  const prevActiveTabIdRef = useRef<number | null>(null)
  useEffect(() => {
    const prev = prevActiveTabIdRef.current
    prevActiveTabIdRef.current = activeTabId
    if (prev != null && prev !== activeTabId && editLockHeldRef.current[prev] === true) {
      void (async () => {
        const script = dirtyMapRef.current[prev]
        if (script !== undefined && wsId != null) {
          writeScriptLocalDraft(scriptDraftStorageKey(`studio.${wsId}`, prev), script)
        }
        const ok = await flushDraftToServer(prev)
        if (ok) {
          studioApi.releaseEditLock(prev).catch(() => {})
          setEditLockHeld(p => {
            const n = { ...p }
            delete n[prev]
            return n
          })
        } else if (script !== undefined) {
          message.warning('上一脚本草稿同步失败，已保留本地且未释放编辑锁')
        } else {
          studioApi.releaseEditLock(prev).catch(() => {})
          setEditLockHeld(p => {
            const n = { ...p }
            delete n[prev]
            return n
          })
        }
      })()
    }
  }, [activeTabId, flushDraftToServer, wsId])

  // 关闭 tab（支持批量，类似 IDEA）：先冲刷草稿；失败则保留 dirty/锁并写本地
  const closeTabsBulk = (ids: number[]) => {
    if (!ids.length) return
    const idSet = new Set(ids)
    const prev = openTabs
    const newTabs = prev.filter(t => !idSet.has(t.id))
    let nextActive = activeTabId
    if (activeTabId != null && idSet.has(activeTabId)) {
      const oldIdx = prev.findIndex(t => t.id === activeTabId)
      nextActive = null
      for (let i = oldIdx - 1; i >= 0; i--) {
        if (!idSet.has(prev[i].id)) {
          nextActive = prev[i].id
          break
        }
      }
      if (nextActive == null) {
        for (let i = oldIdx + 1; i < prev.length; i++) {
          if (!idSet.has(prev[i].id)) {
            nextActive = prev[i].id
            break
          }
        }
      }
    }
    void (async () => {
      const failed: number[] = []
      for (const id of ids) {
        const script = dirtyMapRef.current[id]
        if (script !== undefined && wsId != null) {
          writeScriptLocalDraft(scriptDraftStorageKey(`studio.${wsId}`, id), script)
        }
        if (editLockHeldRef.current[id] === true) {
          const ok = await flushDraftToServer(id)
          if (ok) {
            studioApi.releaseEditLock(id).catch(() => {})
            setEditLockHeld(prevHeld => {
              const n = { ...prevHeld }
              delete n[id]
              return n
            })
          } else {
            failed.push(id)
          }
        } else if (script !== undefined) {
          // 无锁脏内容：已写本地，从内存清掉（重开可 restore）
          setDirtyMap(prevDirty => {
            if (prevDirty[id] === undefined) return prevDirty
            const n = { ...prevDirty }
            delete n[id]
            return n
          })
        }
      }
      if (failed.length) {
        message.warning(
          failed.length === 1
            ? '脚本草稿未能同步到服务端，已保留本地且未释放编辑锁；重新打开可继续编辑'
            : `${failed.length} 个脚本草稿未能同步，已保留本地且未释放编辑锁`,
        )
      }
    })()
    setOpenTabs(newTabs)
    if (nextActive !== activeTabId) setActiveTabId(nextActive)
    setResultMap(prevMap => {
      const n = { ...prevMap }
      ids.forEach(id => { delete n[id] })
      return n
    })
    setLogMap(prevMap => {
      const n = { ...prevMap }
      ids.forEach(id => { delete n[id] })
      return n
    })
    setTabContentLoading(prev => {
      let changed = false
      const n = { ...prev }
      ids.forEach(id => {
        if (n[id] !== undefined) {
          delete n[id]
          changed = true
        }
      })
      return changed ? n : prev
    })
    setTabContentError(prev => {
      let changed = false
      const n = { ...prev }
      ids.forEach(id => {
        if (n[id] !== undefined) {
          delete n[id]
          changed = true
        }
      })
      return changed ? n : prev
    })
  }

  const closeTab = (nodeId: number) => closeTabsBulk([nodeId])

  // 当前激活节点
  const activeNode = openTabs.find(t => t.id === activeTabId)
  const activeContentError = activeTabId != null ? tabContentError[activeTabId] : undefined
  const activeContentPending = Boolean(
    activeNode
    && !activeContentError
    && (tabContentLoading[activeNode.id] || isEditorTabContentPending(activeNode)),
  )
  const activeScript = activeTabId !== null
    ? (dirtyMap[activeTabId] ?? activeNode?.script_content ?? '')
    : ''
  const holdsEditLock = activeTabId !== null && editLockHeld[activeTabId] === true
  const canEdit = Boolean(
    canWrite && activeNode && !activeNode.is_locked && holdsEditLock && !activeContentPending,
  )
  const isDirty = activeTabId !== null && dirtyMap[activeTabId] !== undefined
  const studioDraftKey =
    wsId != null && activeTabId != null ? scriptDraftStorageKey(`studio.${wsId}`, activeTabId) : null

  const scriptAutosave = useScriptAutosave({
    enabled: Boolean(canEdit && activeTabId != null && wsId != null),
    dirty: isDirty,
    value: activeScript,
    storageKey: studioDraftKey,
    entityId: activeTabId,
    persist: async (script, entityId) => {
      const nodeId = entityId == null ? null : Number(entityId)
      if (!wsId || nodeId == null || !Number.isFinite(nodeId)) throw new Error('no active node')
      const tab = openTabsRef.current.find(t => t.id === nodeId)
      if (!tab) throw new Error('no active node')
      const updated: any = await studioApi.saveDraft(nodeId, {
        workspace_id: wsId,
        name: tab.name,
        node_type: tab.node_type,
        script_content: script,
      })
      if (dirtyMapRef.current[nodeId] !== script) return
      setNodes(prev => prev.map(n => (n.id === nodeId ? { ...n, ...updated, script_content: script } : n)))
      setOpenTabs(prev => prev.map(t => (t.id === nodeId ? { ...t, ...updated, script_content: script } : t)))
    },
    onSynced: (script, entityId) => {
      // 按保存时的 nodeId 清 dirty（即使已切到其他 Tab）
      if (entityId == null) return
      const nodeId = Number(entityId)
      if (!Number.isFinite(nodeId) || dirtyMapRef.current[nodeId] !== script) return
      setDirtyMap(prev => {
        if (prev[nodeId] !== script) return prev
        const n = { ...prev }
        delete n[nodeId]
        return n
      })
    },
    persistKeepalive: () => {
      const id = activeTabIdRef.current
      if (id != null) flushDraftKeepalive(id)
    },
  })

  const tabVersionDirtyMap = useMemo(
    () => Object.fromEntries(openTabs.map(t => [t.id, scriptAutosave.isVersionDirty(t.id)])),
    [openTabs, scriptAutosave.isVersionDirty, scriptAutosave.versionDirtyEpoch],
  )

  const resultColSig =
    activeTabId != null && resultMap[activeTabId]?.columns
      ? resultMap[activeTabId]!.columns.join('\x1e')
      : ''

  useEffect(() => {
    if (activeTabId == null) {
      setResultColMeta({ order: [], widths: {} })
      return
    }
    const stored = loadStudioResultMetaMap()[String(activeTabId)] ?? { order: [], widths: {} }
    const cols = resultMap[activeTabId]?.columns
    if (!cols?.length) {
      setResultColMeta(stored)
      return
    }
    const next: StudioResultColMeta = {
      order: resolveResultColumnOrder(stored.order, cols, stored.sourceKeys),
      widths: pruneWidths(stored.widths, cols),
      sourceKeys: cols,
    }
    setResultColMeta(next)
    // 列签名变化时写回，避免旧 order 持续污染后续查询
    if (
      !stored.sourceKeys?.length ||
      stored.sourceKeys.join('\x1e') !== cols.join('\x1e') ||
      stored.order.join('\x1e') !== next.order.join('\x1e')
    ) {
      saveStudioResultMetaNode(activeTabId, next)
    }
  }, [activeTabId, resultColSig])

  const onResultColumnOrderChange = useCallback(
    (nextOrder: string[]) => {
      if (activeTabId == null) return
      setResultColMeta(prev => {
        const cols = resultMap[activeTabId]?.columns ?? prev.sourceKeys ?? nextOrder
        const next = { ...prev, order: nextOrder, sourceKeys: cols }
        saveStudioResultMetaNode(activeTabId, next)
        return next
      })
    },
    [activeTabId, resultMap],
  )

  const onResultColumnWidthChange = useCallback(
    (key: string, width: number) => {
      if (activeTabId == null) return
      setResultColMeta(prev => {
        const next = { ...prev, widths: { ...prev.widths, [key]: width } }
        saveStudioResultMetaNode(activeTabId, next)
        return next
      })
    },
    [activeTabId],
  )

  /**
   * 协作编辑锁：仅有写权限时在点击/聚焦脚本区或显式写操作时占用。
   * 只读角色（运维/分析等）绝不抢锁、绝不因点选脚本弹权限提示（成熟产品：静默只读）。
   */
  const acquireLockPromiseRef = useRef<Promise<boolean> | null>(null)
  const requestEditLockOnInteraction = useCallback(
    async (opts?: { silent?: boolean }): Promise<boolean> => {
      const silent = opts?.silent ?? false
      if (activeTabId == null || !activeNode || activeNode.is_locked) return false
      // 无写权限：静默拒绝。提示只留给显式「新建 / 保存 / 格式化」等入口。
      if (!canWrite) return false
      const tabId = activeTabId
      if (editLockHeldRef.current[tabId] === true) return true
      if (acquireLockPromiseRef.current) return acquireLockPromiseRef.current
      const p = (async (): Promise<boolean> => {
        try {
          const res: any = await studioApi.acquireEditLock(tabId)
          if (activeTabIdRef.current !== tabId) {
            studioApi.releaseEditLock(tabId).catch(() => {})
            return false
          }
          const n = res.node
          setEditLockHeld(prev => ({ ...prev, [tabId]: true }))
          setNodes(prev => prev.map(x => (x.id === n.id ? { ...x, ...n } : x)))
          setOpenTabs(prev => prev.map(t => (t.id === n.id ? { ...t, ...n } : t)))
          return true
        } catch (e: any) {
          if (activeTabIdRef.current === tabId) {
            setEditLockHeld(prev => ({ ...prev, [tabId]: false }))
          }
          if (!silent) {
            if (e?.response?.status === 409) {
              message.warning(e?.response?.data?.detail || '脚本正由他人编辑，如需编辑请使用「抢锁编辑」')
            } else if (e?.response?.status === 403) {
              message.error(e?.response?.data?.detail || '无脚本编辑权限')
            } else if (e?.response?.status !== 401) {
              message.error(e?.response?.data?.detail || '无法获取编辑锁')
            }
          }
          return false
        } finally {
          acquireLockPromiseRef.current = null
        }
      })()
      acquireLockPromiseRef.current = p
      return p
    },
    [activeTabId, activeNode, canWrite],
  )

  const handleEditorAreaPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return
      if (!canWrite) return
      if (activeTabId == null || !activeNode || activeNode.is_locked) return
      if (editLockHeldRef.current[activeTabId] === true) return
      void requestEditLockOnInteraction()
    },
    [canWrite, activeTabId, activeNode, requestEditLockOnInteraction],
  )

  const handleEditorAreaFocusCapture = useCallback(() => {
    if (!canWrite) return
    if (activeTabId == null || !activeNode || activeNode.is_locked) return
    if (editLockHeldRef.current[activeTabId] === true) return
    void requestEditLockOnInteraction()
  }, [canWrite, activeTabId, activeNode, requestEditLockOnInteraction])

  const onEditorChange = (val: string | undefined) => {
    if (activeTabId === null || !canEdit) return
    setDirtyMap(prev => ({ ...prev, [activeTabId]: val ?? '' }))
  }

  // 显式「保存版本」：写入服务端并生成版本历史
  const handleSave = async (): Promise<boolean> => {
    if (!activeNode) return false
    if (!canWrite) {
      message.warning('当前角色无数据开发编辑权限，无法保存')
      return false
    }
    if (activeNode.is_locked) {
      message.warning('脚本已锁定，无法保存')
      return false
    }
    let ok = activeTabId != null && editLockHeld[activeTabId] === true
    if (!ok) {
      ok = await requestEditLockOnInteraction({ silent: true })
    }
    if (!ok) {
      message.warning('请先点击脚本编辑区获取编辑锁后再保存；若当前由他人占用请使用「抢锁编辑」')
      return false
    }
    const script = dirtyMap[activeTabId!] ?? activeNode.script_content
    let updated: any
    try {
      updated = await studioApi.updateNode(
        activeNode.id,
        { ...activeNode, script_content: script, workspace_id: wsId },
        { createHistory: true },
      )
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
      return false
    }
    const nu = updated as any
    setNodes(prev => prev.map(n => n.id === activeNode.id ? { ...n, ...nu, script_content: script } : n))
    setOpenTabs(prev => prev.map(t => t.id === activeNode.id ? { ...t, ...nu, script_content: script } : t))
    setDirtyMap(prev => { const n = { ...prev }; delete n[activeNode.id]; return n })
    scriptAutosave.markVersionSaved()
    message.success('已保存并记入版本历史')
    return true
  }

  // 运行（直接用编辑器最新内容，不需要先保存）
  const dsResolve = useMemo(() => {
    if (!activeNode || (activeNode.node_type !== 'SQL' && activeNode.node_type !== 'PYTHON')) return null
    return resolveDatasourceForRun(
      activeNode.datasource_id,
      currentWorkspace,
      datasources,
    )
  }, [activeNode, currentWorkspace, datasources])

  const sqlDefaultCatalog = useMemo(() => {
    const id = dsResolve?.effectiveId
    if (id == null) return null
    const ds = datasources.find((d: any) => d.id === id)
    return (ds?.database || null) as string | null
  }, [dsResolve?.effectiveId, datasources])

  const { bindSqlSchemaCompletion } = useSqlSchemaCompletion({
    datasourceId: activeNode?.node_type === 'SQL' ? dsResolve?.effectiveId : null,
    defaultCatalog: sqlDefaultCatalog,
  })

  const handleRun = async (overrideScript?: string, meta?: { fromSelection?: boolean }) => {
    if (!activeNode) return
    if (activeNode.node_type === 'SQL' && !dsResolve?.effectiveId) {
      message.warning('请先在「空间设置」配置默认数据源，或在节点「配置」中单独指定')
      return
    }
    if (activeNode.node_type === 'PYTHON' && !dsResolve?.effectiveId) {
      message.warning('未配置数据源时 job.execute 将失败；请绑定节点数据源或设置空间默认（仅 writelog 可继续）')
    }
    const latestScript = overrideScript
      ?? (dirtyMap[activeTabId!] ?? activeNode.script_content ?? '')
    if (meta?.fromSelection) {
      message.info('已执行选中片段')
    }
    setRunningId(activeNode.id)
    setLogMap(prev => ({ ...prev, [activeNode.id]: '' }))
    setResultMap(prev => ({ ...prev, [activeNode.id]: null }))
    setLogPanelOpen(true)
    setResultTab(prev => ({ ...prev, [activeNode.id]: activeNode.node_type === 'SQL' ? 'result' : 'log' }))
    try {
      const res: any = await studioApi.runNode(
        activeNode.id,
        latestScript,
        runBizdate ? runBizdate.format('YYYY-MM-DD') : undefined,
      )
      setLogMap(prev => ({ ...prev, [activeNode.id]: res.log || '执行完成，无输出' }))
      if (res.result) setResultMap(prev => ({ ...prev, [activeNode.id]: res.result }))
    } catch (e: any) {
      setLogMap(prev => ({ ...prev, [activeNode.id]: e?.response?.data?.detail || '执行失败' }))
      setResultTab(prev => ({ ...prev, [activeNode.id]: 'log' }))
    }
    setRunningId(null)
  }

  const handleRunRef = useRef(handleRun)
  handleRunRef.current = handleRun

  // 发布
  const handlePublish = async () => {
    if (!activeNode) return
    if (activeNode.is_locked) {
      message.info('已处于锁定状态')
      return
    }
    if (!(await handleSave())) return
    if (!canPublishDirect) {
      setApprovalNote('')
      setApprovalModalOpen(true)
      return
    }
    const pub: any = await studioApi.publishNode(activeNode.id)
    const nu = pub?.node
    if (nu) {
      setNodes(prev => prev.map(n => n.id === nu.id ? { ...n, ...nu } : n))
      setOpenTabs(prev => prev.map(t => t.id === nu.id ? { ...t, ...nu } : t))
    } else {
      setNodes(prev => prev.map(n => n.id === activeNode.id ? { ...n, is_published: true, is_locked: true } : n))
      setOpenTabs(prev => prev.map(t => t.id === activeNode.id ? { ...t, is_published: true, is_locked: true } : t))
    }
    message.success('已提交，脚本已锁定（GIDO 发布治理）')
    await load()
  }

  const submitPublishApproval = async () => {
    if (!activeNode || !wsId) return
    try {
      await approvalApi.submit({
        workspace_id: wsId,
        resource_type: 'studio_node',
        resource_id: activeNode.id,
        action: 'publish_node',
        submit_note: approvalNote || undefined,
      })
      message.success('已提交审批，通过后脚本将自动锁定')
      setApprovalModalOpen(false)
      setApprovalNote('')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败')
    }
  }

  const isNodePendingApproval = activeNode
    ? pendingKeys.has(`studio_node:${activeNode.id}:publish_node`)
    : false

  const handleUnlock = async () => {
    if (!activeNode) return
    try {
      const res: any = await studioApi.unlockNode(activeNode.id)
      const nu = res?.node
      if (nu) {
        setNodes(prev => prev.map(n => n.id === nu.id ? { ...n, ...nu } : n))
        setOpenTabs(prev => prev.map(t => t.id === nu.id ? { ...t, ...nu } : t))
      }
      message.success('已解锁，可继续编辑')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '解锁失败')
    }
  }

  // 删除节点
  const handleDelete = async (nodeId: number) => {
    await studioApi.deleteNode(nodeId)
    closeTab(nodeId)
    load()
    message.success('删除成功')
  }

  // SQL 格式化
  const handleFormat = async () => {
    if (!activeNode || activeNode.node_type !== 'SQL') return
    if (!canWrite) {
      message.warning('当前角色无数据开发编辑权限，无法格式化')
      return
    }
    if (activeNode.is_locked) {
      message.warning('脚本已锁定，无法格式化')
      return
    }
    let ok = activeTabId != null && editLockHeld[activeTabId] === true
    if (!ok) {
      ok = await requestEditLockOnInteraction({ silent: true })
    }
    if (!ok) {
      message.warning('请先点击脚本编辑区获取编辑锁后再格式化；若当前由他人占用请使用「抢锁编辑」')
      return
    }
    const current = dirtyMap[activeTabId!] ?? activeNode.script_content ?? ''
    const dsType = String(
      datasources.find((d: any) => d.id === dsResolve?.effectiveId)?.ds_type || '',
    ).toLowerCase()
    const language = dsType === 'postgresql' || dsType === 'postgres' ? 'postgresql' : 'mysql'
    try {
      const formatted = sqlFormat(current, { language, tabWidth: 2, keywordCase: 'upper' })
      setDirtyMap(prev => ({ ...prev, [activeTabId!]: formatted }))
    } catch {
      message.warning('格式化失败，请检查 SQL 语法')
    }
  }

  // 新建文件夹
  const handleCreateFolder = async () => {
    const values = await folderForm.validateFields()
    await studioApi.createFolder({ workspace_id: wsId, name: values.name, parent_id: folderParentId })
    setFolderModal(false)
    folderForm.resetFields()
    setFolderParentId(null)
    await load()
    message.success('文件夹创建成功')
  }

  // 删除文件夹
  const handleDeleteFolder = async (folderId: number) => {
    await studioApi.deleteFolder(folderId)
    await load()
    message.success('删除成功')
  }

  // 新建节点
  const handleCreate = async () => {
    if (!canWrite) {
      message.warning('当前角色无数据开发编辑权限，无法新建节点')
      return
    }
    try {
      const values = await createForm.validateFields()
      values.workspace_id = wsId
      values.folder_id = createFolderId
      values.script_content = values.node_type === 'SQL'
        ? buildDefaultSqlPublishScript({
            scriptName: values.name,
            author: user?.username || user?.full_name || '',
            jobName: values.name,
          })
        : values.node_type === 'PYTHON'
          ? [
              'from gido_job import job',
              '',
              'job.writelog("start")',
              '# 全局变量：源码 "${var_key}" 跑前展开；或 job.var("var_key")',
              '# webhook = "${LARK_WEBHOOK_URL}"',
              '# webhook = job.var("LARK_WEBHOOK_URL", default="")',
              'rows = job.execute("SELECT 1 AS n")',
              'job.writelog(f"rows={len(rows)}")',
              'for r in rows:',
              '    job.writelog(r)',
            ].join('\n')
          : values.node_type === 'SYNC'
            ? '{"sync_task_id": null}'
            : values.node_type === 'DEPENDENT'
              ? '# DEPENDENT：等待其他工作流成功（无脚本，请在节点配置中选择依赖工作流）\n'
              : values.node_type === 'VIRTUAL'
                ? '# VIRTUAL\n'
                : '#!/bin/bash\necho "hello gido"'
      if (values.node_type === 'SYNC') {
        values.params = { sync_task_id: null }
      }
      if (values.node_type === 'DEPENDENT') {
        values.params = {
          relation: 'AND',
          depend_items: [{ depend_workflow_id: null, cycle: 'day', date_value: 'today' }],
          depend_workflow_id: null,
          cycle: 'day',
          date_value: 'today',
        }
      }
      if ((values.node_type === 'SQL' || values.node_type === 'PYTHON') && !values.datasource_id) {
        delete values.datasource_id
      }
      const node: any = await studioApi.createNode(values)
      setCreateModal(false)
      createForm.resetFields()
      setCreateFolderId(null)
      await load()
      openNode(node)
      message.success('创建成功')
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || '创建失败')
    }
  }

  // 打开节点配置（与工作流 DAG 共用 NodeConfigModal）
  const openConfig = () => {
    if (!activeNode) return
    setConfigModal(true)
  }

  const openHistory = async () => {
    if (!activeNode) return
    const res: any = await studioApi.getHistory(activeNode.id)
    setHistoryList(res as any[])
    setHistoryModal(true)
  }

  const handleRollback = async (historyId: number, script: string) => {
    if (!activeNode) return
    if (activeNode.is_locked) {
      message.warning('脚本已锁定，无法回滚')
      return
    }
    let ok = activeTabId != null && editLockHeld[activeTabId] === true
    if (!ok) {
      ok = await requestEditLockOnInteraction({ silent: true })
    }
    if (!ok) {
      message.warning('请先点击脚本编辑区获取编辑锁后再回滚；若当前由他人占用请使用「抢锁编辑」')
      return
    }
    await studioApi.rollback(activeNode.id, historyId)
    setNodes(prev => prev.map(n => n.id === activeNode.id ? { ...n, script_content: script } : n))
    setOpenTabs(prev => prev.map(t => t.id === activeNode.id ? { ...t, script_content: script } : t))
    setDirtyMap(prev => ({ ...prev, [activeNode.id]: script }))
    setHistoryModal(false)
    message.success('已回滚到该版本')
  }

  const handleStealEditLock = () => {
    if (!activeNode) return
    Modal.confirm({
      title: '抢锁编辑',
      content: `当前编辑锁由「${activeNode.edit_lock_username || '其他用户'}」持有，确定抢占？对方未保存的本地修改不受影响。`,
      okText: '抢锁',
      onOk: async () => {
        const res: any = await studioApi.acquireEditLock(activeNode.id, true)
        const n = res.node
        setEditLockHeld(prev => ({ ...prev, [activeNode.id]: true }))
        setNodes(prev => prev.map(x => (x.id === n.id ? { ...x, ...n } : x)))
        setOpenTabs(prev => prev.map(t => (t.id === n.id ? { ...t, ...n } : t)))
        message.success('已抢占编辑锁')
      },
    })
  }

  const isRunning = activeTabId !== null && runningId === activeTabId

  const renderScriptPane = () => {
    if (activeNode?.node_type === 'SYNC') {
      return (
        <div style={{ padding: 16, color: '#666', fontSize: 13, lineHeight: 1.6, overflow: 'auto', height: '100%' }}>
          <p><strong>SYNC 节点</strong>：运行「数据集成」中的同步任务，无需编写 SQL。</p>
          <p>在「配置」里选择集成任务；加入工作流后随 DAG 调度，或由 Dolphin 通过内部 API 触发。</p>
          <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, marginTop: 8 }}>
            {activeScript || '{"sync_task_id": null}'}
          </pre>
        </div>
      )
    }
    if (activeNode?.node_type === 'DEPENDENT') {
      const p = (activeNode.params && typeof activeNode.params === 'object') ? activeNode.params : {}
      const items = Array.isArray(p.depend_items) && p.depend_items.length
        ? p.depend_items
        : [{ depend_workflow_id: p.depend_workflow_id, date_value: p.date_value || 'today', cycle: p.cycle || 'day' }]
      const preview = {
        relation: p.relation || 'AND',
        depend_items: items.map((it: any) => ({
          depend_workflow_id: it.depend_workflow_id ?? null,
          depend_workflow_name: workflows.find((w: any) => w.id === it.depend_workflow_id)?.name || null,
          cycle: it.cycle || 'day',
          date_value: it.date_value || 'today',
        })),
      }
      return (
        <div style={{ padding: 16, color: '#666', fontSize: 13, lineHeight: 1.6, overflow: 'auto', height: '100%' }}>
          <p><strong>DEPENDENT 节点</strong>：等待同空间其他工作流整流程在指定时段成功，无需编写脚本。</p>
          <p>在「配置」里可添加多条依赖（AND/OR）与丰富时段；发布前请先发布被依赖工作流。生产侧按 Dolphin 窗口内最近成功实例判断。</p>
          <pre style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, marginTop: 8 }}>
            {JSON.stringify(preview, null, 2)}
          </pre>
        </div>
      )
    }
    return (
      <div style={{ position: 'relative', height: '100%' }}>
        {activeContentError ? (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2,
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            gap: 12, background: 'rgba(255,255,255,0.88)', padding: 24,
          }}
          >
            <ExclamationCircleOutlined style={{ fontSize: 28, color: '#faad14' }} />
            <div style={{ color: '#595959', maxWidth: 420, textAlign: 'center', fontSize: 13 }}>
              {activeContentError}
            </div>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              data-testid="studio-tab-content-retry"
              onClick={() => {
                if (activeTabId != null) {
                  void ensureTabContent(activeTabId, { activate: true, force: true })
                }
              }}
            >
              重新加载
            </Button>
          </div>
        ) : activeContentPending ? (
          <div style={{
            position: 'absolute', inset: 0, zIndex: 2,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(255,255,255,0.72)',
          }}
          >
            <Spin tip="加载脚本…" />
          </div>
        ) : null}
        <MonacoFindBar
          getEditor={() => editorRef.current}
          apiRef={findApiRef}
          readOnly={!canEdit}
          theme={editorAppearance.theme}
        />
        <Editor
          key={activeTabId ?? 0}
          height="100%"
          language={LANG_MAP[activeNode!.node_type] || 'plaintext'}
          value={activeContentPending ? '' : activeScript}
          onChange={!canEdit ? undefined : onEditorChange}
          beforeMount={registerDwMonacoThemes}
          onMount={(editor, monaco) => {
            editorRef.current = editor
            bindMonacoFindKeybindings(editor, monaco, () => findApiRef.current)
            bindMonacoScriptKeybindings(editor, monaco, {
              enableRun: () => {
                const id = activeTabIdRef.current
                const n = openTabsRef.current.find(t => t.id === id)
                return canRunStudioTabShortcut({
                  canRun,
                  node: n,
                  loading: id != null ? tabContentLoadingRef.current[id] : false,
                  error: id != null ? tabContentErrorRef.current[id] : null,
                  running: id != null && runningIdRef.current === id,
                })
              },
              onRun: (script, meta) => {
                void handleRunRef.current(script, meta)
              },
            })
            if (activeNode?.node_type === 'SQL') {
              bindSqlSchemaCompletion(editor, monaco)
            }
          }}
          theme={editorAppearance.theme}
          options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), readOnly: Boolean(!canEdit || activeContentPending) }}
        />
      </div>
    )
  }

  const editorCaptureProps = (activeNode?.node_type === 'SYNC' || activeNode?.node_type === 'DEPENDENT')
    ? {}
    : { onPointerDownCapture: handleEditorAreaPointerDown, onFocusCapture: handleEditorAreaFocusCapture }

  return (
    <>
    <StudioWorkbenchShell
      storageKey="gido.studio.sidebarWidth"
      defaultWidth={240}
      minWidth={180}
      maxWidth={560}
      collapsed={sidebarCollapsed}
      sidebarTitle={BRAND.offline}
      sidebarActions={(
        <>
          <Tooltip title={canWrite ? '新建目录' : '无编辑权限'}>
            <Button type="text" size="small" icon={<FolderAddOutlined />} disabled={!canWrite} onClick={() => { setFolderParentId(null); setFolderModal(true) }} />
          </Tooltip>
          <Tooltip title={canWrite ? '新建节点' : '无编辑权限'}>
            <Button type="text" size="small" icon={<PlusOutlined />} disabled={!canWrite} onClick={() => { setCreateFolderId(null); setCreateModal(true) }} />
          </Tooltip>
          <Tooltip title="隐藏节点列表">
            <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setSidebarCollapsedPersist(true)} />
          </Tooltip>
        </>
      )}
      treeBodyClassName="studio-node-tree"
      tree={(
          <WorkspaceFolderTree
            rootTitle="节点列表"
            treeClassName="studio-node-tree"
            showRootCreateButton={false}
            readOnly={!canWrite}
            folders={folders}
            leaves={nodes}
            expandedKeys={treeExpandedKeys}
            onExpandedKeysChange={setTreeExpandedKeys}
            selectedLeafId={activeTabId}
            onSelectLeaf={openNode}
            onCreateFolder={parentId => {
              if (!canWrite) {
                message.warning('当前角色无数据开发编辑权限')
                return
              }
              setFolderParentId(parentId)
              setFolderModal(true)
            }}
            onRenameFolder={async (id, name) => {
              await studioApi.renameFolder(id, name)
              await load()
            }}
            onDeleteFolder={async id => {
              await handleDeleteFolder(id)
            }}
            onRenameLeaf={async (id, name) => {
              const node = nodes.find(n => n.id === id)
              if (!node) return
              if (node.is_locked) {
                message.warning('脚本已锁定，请先解锁后再重命名')
                return
              }
              await studioApi.updateNode(id, { ...node, name, workspace_id: wsId })
              setNodes(prev => prev.map(n => (n.id === id ? { ...n, name } : n)))
              setOpenTabs(prev => prev.map(t => (t.id === id ? { ...t, name } : t)))
            }}
            onDeleteLeaf={leaf => {
              Modal.confirm({
                title: '删除脚本？',
                content: leaf.name,
                onOk: () => handleDelete(leaf.id),
              })
            }}
            onMoveAndReorder={async ({ leafId, targetFolderId, orderedLeafIds, folderChanged }) => {
              if (!wsId) return
              if (folderChanged) await studioApi.moveNodeFolder(leafId, targetFolderId)
              await studioApi.reorderNodes(wsId, targetFolderId, orderedLeafIds)
              message.success('已移动')
              await load()
            }}
            onMoveFolder={async ({ folderId, targetParentId }) => {
              await studioApi.moveFolderParent(folderId, targetParentId)
              await load()
            }}
            folderMenuExtra={f => canWrite ? [
              { key: 'add-node', label: '新建节点', onClick: () => { setCreateFolderId(f.id); setCreateModal(true) } },
            ] : []}
          />
      )}
    >
      <StudioWorkbenchTopStrip>
        <StudioWorkbenchExpandSidebarButton
          collapsed={sidebarCollapsed}
          onExpand={() => setSidebarCollapsedPersist(false)}
          tooltip="显示节点列表"
        />
          <StudioEditorTabStrip
            tabs={openTabs}
            activeTabId={activeTabId}
            versionDirtyMap={tabVersionDirtyMap}
            tabContentLoading={tabContentLoading}
            tabContentError={tabContentError}
            onActivate={activateTab}
            onClose={closeTab}
            onReload={(tabId) => {
              setActiveTabId(tabId)
              void ensureTabContent(tabId, { activate: true, force: true })
            }}
            onCloseOthers={(tabId) => closeTabsBulk(openTabs.filter(t => t.id !== tabId).map(t => t.id))}
            onCloseLeft={(tabId) => {
              const idx = openTabs.findIndex(t => t.id === tabId)
              if (idx > 0) closeTabsBulk(openTabs.slice(0, idx).map(t => t.id))
            }}
            onCloseRight={(tabId) => {
              const idx = openTabs.findIndex(t => t.id === tabId)
              if (idx >= 0 && idx < openTabs.length - 1) {
                closeTabsBulk(openTabs.slice(idx + 1).map(t => t.id))
              }
            }}
            onCloseAll={() => closeTabsBulk(openTabs.map(t => t.id))}
          />
      </StudioWorkbenchTopStrip>

        {activeNode ? (
          <>
            <StudioWorkbenchToolbar>
              {!canWrite && (
                <Tooltip title="可查看与运行（若有权限），不能新建或修改脚本">
                  <Tag style={{ margin: 0 }}>只读</Tag>
                </Tooltip>
              )}
              <StudioWorkbenchActiveEntityTitle
                name={activeNode.name}
                variant="chip"
                testId="studio-active-script-title"
              />
              <Button
                type="primary"
                icon={isRunning ? <LoadingOutlined /> : <PlayCircleOutlined />}
                onClick={() => { void handleRun() }}
                disabled={isRunning || !canRun || activeContentPending || Boolean(activeContentError)}
                size="small"
                title={canRun ? undefined : '无运行权限'}
              >
                {isRunning ? '运行中...' : '运行'}
              </Button>
              <Button
                icon={<SaveOutlined />}
                onClick={handleSave}
                size="small"
                type={scriptAutosave.versionDirty ? 'default' : 'text'}
                disabled={!canWrite || activeNode.is_locked}
                title="写入服务端并生成版本历史（后台自动落草稿，不记版本、无打扰提示）"
              >
                保存版本{scriptAutosave.versionDirty ? ' *' : ''}
              </Button>
              <AutosaveStatusHint
                visible={canEdit}
                status={scriptAutosave.status}
                hint={scriptAutosave.hint}
              />
              {activeNode?.node_type === 'SQL' && (
                <Button icon={<FormatPainterOutlined />} onClick={() => void handleFormat()} size="small" disabled={!canWrite || activeNode.is_locked}>格式化</Button>
              )}
              {activeNode?.node_type === 'SQL' && (
                <Button
                  icon={<TableOutlined />}
                  size="small"
                  onClick={() => setSchemaBrowserOpen(true)}
                  disabled={!dsResolve?.effectiveId}
                  title={dsResolve?.effectiveId ? '浏览库表并插入' : '请先绑定数据源'}
                >
                  库表
                </Button>
              )}
              {(activeNode?.node_type === 'SQL' || activeNode?.node_type === 'PYTHON') && (
                <Tooltip title="试跑业务日期（宏 ${bizdate}）；清空则用当天">
                  <DatePicker
                    size="small"
                    allowClear
                    value={runBizdate}
                    onChange={v => setRunBizdate(v)}
                    style={{ width: 140 }}
                    placeholder="业务日期"
                  />
                </Tooltip>
              )}
              {(activeNode?.node_type === 'SQL' || activeNode?.node_type === 'PYTHON') && dsResolve && (
                <Tag
                  color={dsResolve.effectiveId ? (dsResolve.source === 'explicit' ? 'purple' : 'blue') : 'red'}
                  title={
                    dsResolve.source === 'explicit'
                      ? '此节点在「配置」中单独指定了数据源，不随空间默认变更'
                      : '未单独指定，运行时使用空间设置中的默认数据源'
                  }
                >
                  {datasourceTagText(dsResolve)}
                </Tag>
              )}
              <Button
                icon={<CloudUploadOutlined />}
                onClick={handlePublish}
                size="small"
                title={canPublishDirect ? '提交后锁定脚本，需解锁再改' : '提交审批，管理员通过后锁定脚本'}
                disabled={!canWrite || activeNode.is_locked || isNodePendingApproval}
              >
                {isNodePendingApproval ? '审批中' : canPublishDirect ? '提交' : '提交审批'}
              </Button>
              {activeNode.is_locked && canWrite && (
                <Button icon={<UnlockOutlined />} size="small" onClick={handleUnlock}>
                  解锁
                </Button>
              )}
              {canWrite && !canEdit && !activeNode.is_locked && activeNode.edit_lock_username && (
                <Button size="small" danger icon={<LockOutlined />} onClick={handleStealEditLock}>抢锁编辑</Button>
              )}
              <Button icon={<SettingOutlined />} onClick={openConfig} size="small" disabled={!canWrite && !canRun}>配置</Button>
              <Button
                icon={<AimOutlined />}
                onClick={locateActiveInTree}
                size="small"
                title="在节点列表中定位当前脚本"
              >
                定位
              </Button>
              <Button icon={<ReloadOutlined />} onClick={openHistory} size="small">版本历史</Button>
              <Button
                icon={<ScheduleOutlined />}
                size="small"
                title="到运维中心查看调度实例（Studio 提交≠上线调度）"
                onClick={() => navigate(R.batch.operation)}
              >
                运维
              </Button>
              <div style={{ flex: 1 }} />
              <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
              <Tag color={activeNode.is_locked ? 'orange' : activeNode.is_published ? 'green' : 'default'}>
                {activeNode.is_locked ? '已锁定' : activeNode.is_published ? '已提交' : '草稿'}
              </Tag>
              {activeNode.creator_username && (
                <Tag>创建人 {activeNode.creator_username}</Tag>
              )}
              {activeNode.owner_username && (
                <Tag>负责人 {activeNode.owner_username}</Tag>
              )}
              {activeNode.edit_lock_username && (
                <Tag color={holdsEditLock ? 'processing' : 'warning'}>
                  正在编辑 {activeNode.edit_lock_username}{holdsEditLock ? '（我）' : ''}
                </Tag>
              )}
            </StudioWorkbenchToolbar>

            <StudioWorkbenchStage>
                            {logPanelOpen ? (
                <ResizableVerticalSplit
                  storageKey="gido.studio.editorResultSplitRatio"
                  defaultTopRatio={0.58}
                  minTopRatio={0.22}
                  minBottomRatio={0.18}
                  top={(
                    <div style={{ flex: 1, overflow: 'hidden', minHeight: 0 }} {...editorCaptureProps}>
                      {renderScriptPane()}
                    </div>
                  )}
                  bottom={(
                    <EditorResultDock
                      activeKey={resultTab[activeTabId!] ?? 'log'}
                      onChange={key => setResultTab(prev => ({ ...prev, [activeTabId!]: key as 'log' | 'result' }))}
                      onClose={() => setLogPanelOpen(false)}
                      tabs={[
                        {
                          key: 'log',
                          label: <>日志 {isRunning && <Spin size="small" style={{ marginLeft: 6 }} />}</>,
                          children: (
                            <pre style={{
                              flex: 1, margin: 0, padding: '10px 14px',
                              color: '#333', fontSize: 13, overflow: 'auto',
                              whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, monospace',
                              background: '#fff', height: '100%', boxSizing: 'border-box',
                            }}>
                              {isRunning ? '执行中...' : (logMap[activeTabId!] || '暂无日志')}
                            </pre>
                          ),
                        },
                        {
                          key: 'result',
                          label: (
                            <>
                              查询结果
                              {resultMap[activeTabId!] && (
                                <EditorResultRowBadge count={resultMap[activeTabId!]!.total} />
                              )}
                            </>
                          ),
                          children: (
                            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
                              {isRunning && (
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#888' }}>
                                  <Spin /><span style={{ marginLeft: 8 }}>执行中...</span>
                                </div>
                              )}
                              {!isRunning && !resultMap[activeTabId!] && (
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: '#999', fontSize: 13 }}>
                                  运行 SQL 后在此展示结果；表头固定，底部可横向滚动；双击单元格复制
                                </div>
                              )}
                              {!isRunning && resultMap[activeTabId!] && (() => {
                                const { columns, column_types, rows, total } = resultMap[activeTabId!]! as {
                                  columns: string[]
                                  column_types?: string[]
                                  rows: unknown[][]
                                  total: number
                                }
                                const colMetas = normalizeQueryColumns(columns, column_types)
                                const dataSource = rowsToRecordDataSource(columns, rows)
                                const tableColumns = buildQueryTableColumns(colMetas, {
                                  order: resultColMeta.order,
                                  widths: resultColMeta.widths,
                                  dataSource,
                                  onOrderChange: onResultColumnOrderChange,
                                  onWidthChange: onResultColumnWidthChange,
                                })
                                const rawRows = rows as unknown[][]
                                return (
                                  <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
                                    <QueryResultPanel
                                      dataSource={dataSource}
                                      columns={tableColumns}
                                      showViewModeToggle
                                      toolbar={(
                                        <div style={{ padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                          <span style={{ color: '#666', fontSize: 12 }}>
                                            共 <strong>{total}</strong> 行；已返回 <strong>{rows.length}</strong> 行（上限 {SQL_RESULT_ROW_CAP}）；结果区分页展示，表头右上角为类型徽章
                                          </span>
                                          <div style={{ flex: 1 }} />
                                          <Button
                                            size="small"
                                            icon={<DownloadOutlined />}
                                            onClick={() => {
                                              exportRowsToCsv(columns, rawRows, `studio_node_${activeTabId}_result`)
                                              message.success('已导出当前表格数据为 CSV')
                                            }}
                                          >
                                            导出 CSV
                                          </Button>
                                        </div>
                                      )}
                                    />
                                  </div>
                                )
                              })()}
                            </div>
                          ),
                        },
                      ]}
                    />
                  )}
                />
              ) : (
                <div style={{ flex: 1, overflow: 'hidden', minHeight: 0 }} {...editorCaptureProps}>
                  {renderScriptPane()}
                </div>
              )}
            </StudioWorkbenchStage>
          </>
        ) : (
          <StudioWorkbenchEmpty>
            <FileOutlined style={{ fontSize: 48, color: '#bfbfbf' }} />
            <p style={{ fontSize: 14, margin: 0 }}>
              {canWrite ? '从左侧双击节点打开脚本，或新建节点' : '从左侧双击节点打开脚本（当前为只读角色）'}
            </p>
            <Button icon={<PlusOutlined />} disabled={!canWrite} onClick={() => setCreateModal(true)}>新建节点</Button>
          </StudioWorkbenchEmpty>
        )}
    </StudioWorkbenchShell>

      {/* 新建节点弹窗 */}
      <Modal title="新建节点" open={createModal} onOk={handleCreate} onCancel={() => { setCreateModal(false); setCreateFolderId(null) }} width={440}>
        <Form form={createForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="节点名称" rules={[{ required: true }]}>
            <Input placeholder="如：用户日活统计" />
          </Form.Item>
          <Form.Item name="node_type" label="节点类型" rules={[{ required: true }]} initialValue="SQL">
            <Select options={NODE_TYPES.map(t => ({ label: t, value: t }))} />
          </Form.Item>
          <div style={{ color: '#666', fontSize: 12, marginBottom: 12 }}>
            新建 SQL 节点默认继承「空间设置」中的默认数据源；旧节点若在「配置」里指定过数据源则保持不变。需要固定到其它库请在创建后打开「配置」。
          </div>
          {createFolderId && (
            <div style={{ color: '#999', fontSize: 12 }}>将创建在目录：{folders.find(f => f.id === createFolderId)?.name}</div>
          )}
        </Form>
      </Modal>

      {/* 新建文件夹弹窗 */}
      <Modal title="新建目录" open={folderModal} onOk={handleCreateFolder} onCancel={() => { setFolderModal(false); setFolderParentId(null) }} width={360}>
        <Form form={folderForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="目录名称" rules={[{ required: true }]}>
            <Input placeholder="如：用户模块" />
          </Form.Item>
          {folderParentId && (
            <div style={{ color: '#999', fontSize: 12 }}>将创建在：{folders.find(f => f.id === folderParentId)?.name} 下</div>
          )}
        </Form>
      </Modal>

      {/* 版本历史弹窗 */}
      <Modal title="版本历史" open={historyModal} onCancel={() => setHistoryModal(false)} footer={null} width={700}>
        {historyList.length === 0 && <div style={{ color: '#bbb', textAlign: 'center', padding: 24 }}>暂无历史版本</div>}
        {historyList.map((h: any) => (
          <div key={h.id} style={{ marginBottom: 12, border: '1px solid #f0f0f0', borderRadius: 4, padding: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, gap: 8 }}>
              <span style={{ color: '#999', fontSize: 12 }}>{h.saved_at}</span>
              <Space size={4}>
                <Button
                  size="small"
                  icon={<DiffOutlined />}
                  onClick={() => setDiffHistory({ saved_at: h.saved_at, script_content: h.script_content })}
                >
                  对比当前
                </Button>
                <Button size="small" onClick={() => handleRollback(h.id, h.script_content)}>回滚到此版本</Button>
              </Space>
            </div>
            <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 12, maxHeight: 120, overflow: 'auto', margin: 0 }}>
              {h.script_content?.slice(0, 300)}{h.script_content?.length > 300 ? '...' : ''}
            </pre>
          </div>
        ))}
      </Modal>

      <Modal
        title={`对比版本${diffHistory?.saved_at ? ` · ${diffHistory.saved_at}` : ''}`}
        open={Boolean(diffHistory)}
        onCancel={() => setDiffHistory(null)}
        footer={null}
        width={960}
        destroyOnClose
      >
        <div style={{ height: 480 }}>
          <DiffEditor
            original={diffHistory?.script_content || ''}
            modified={
              activeTabId != null
                ? (dirtyMap[activeTabId] ?? activeNode?.script_content ?? '')
                : (activeNode?.script_content ?? '')
            }
            language="sql"
            theme={editorAppearance.theme}
            beforeMount={registerDwMonacoThemes}
            options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false } }}
          />
        </div>
      </Modal>

      <SqlSchemaBrowserDrawer
        open={schemaBrowserOpen}
        onClose={() => setSchemaBrowserOpen(false)}
        datasourceId={dsResolve?.effectiveId}
        defaultCatalog={sqlDefaultCatalog}
        onInsert={(text) => {
          const ed = editorRef.current
          if (!ed) {
            message.warning('请先打开脚本编辑器')
            return
          }
          const sel = ed.getSelection?.()
          if (sel) {
            ed.executeEdits?.('sql-schema-insert', [{ range: sel, text, forceMoveMarkers: true }])
          } else {
            ed.trigger?.('keyboard', 'type', { text })
          }
          ed.focus?.()
        }}
      />

      {wsId != null && (
        <NodeConfigModal
          open={configModal}
          nodeId={activeNode?.id ?? null}
          workspaceId={wsId}
          canWrite={canWrite}
          releaseOnClose={false}
          ensureEditLock={requestEditLockOnInteraction}
          onClose={() => setConfigModal(false)}
          onSaved={(updated) => {
            setNodes(prev => prev.map(n => (n.id === updated.id ? { ...n, ...updated } : n)))
            setOpenTabs(prev => prev.map(t => (t.id === updated.id ? { ...t, ...updated } : t)))
            setDirtyMap(prev => {
              if (prev[updated.id] === undefined) return prev
              const n = { ...prev }
              delete n[updated.id]
              return n
            })
            if (updated.edit_lock_user_id) {
              setEditLockHeld(prev => ({ ...prev, [updated.id]: true }))
            }
          }}
        />
      )}

      <Modal
        title={`提交发布审批 — ${activeNode?.name || ''}`}
        open={approvalModalOpen}
        onOk={submitPublishApproval}
        onCancel={() => { setApprovalModalOpen(false); setApprovalNote('') }}
        okText="提交审批"
      >
        <div style={{ marginBottom: 12, color: '#ad6800', fontSize: 13 }}>
          普通开发不能直接发布到生产。提交后由空间/平台管理员审批，通过后脚本将自动锁定。
        </div>
        <Input.TextArea
          rows={3}
          placeholder="变更说明（可选）"
          value={approvalNote}
          onChange={e => setApprovalNote(e.target.value)}
        />
      </Modal>
    </>
  )
}

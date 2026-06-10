/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, useCallback, useMemo, type PointerEvent } from 'react'
import {
  Tree, Button, Input, Select, Tag, message, Spin, Tooltip,
  Modal, Form, Dropdown, Tabs, Space, Badge, Table
} from 'antd'
import {
  PlayCircleOutlined, SaveOutlined, CloudUploadOutlined, PlusOutlined,
  DeleteOutlined, FileOutlined, FolderOutlined, FolderAddOutlined, MoreOutlined,
  LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ReloadOutlined, SettingOutlined, FormatPainterOutlined, UnlockOutlined,
  LockOutlined, DownloadOutlined,
} from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { format as sqlFormat } from 'sql-formatter'
import { studioApi, datasourceApi, integrationApi, approvalApi } from '../api'
import { BRAND } from '../branding'
import { useAppStore } from '../store'
import { isWorkspaceAdmin } from '../perm'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableSidebar from '../components/ResizableSidebar'
import ResizableVerticalSplit from '../components/ResizableVerticalSplit'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import { normalizeQueryColumns } from '../utils/queryColumns'
import {
  datasourceTagText,
  resolveDatasourceForRun,
} from '../utils/workspaceDatasource'
import QueryResultPanel from '../components/QueryResultPanel'
import { exportRowsToCsv } from '../utils/csvExport'
import { mergeColumnOrderWithKeys, pruneWidths } from '../utils/resultTableMeta'

const NODE_TYPES = ['SQL', 'PYTHON', 'SHELL', 'SYNC', 'VIRTUAL']
const LANG_MAP: Record<string, string> = { SQL: 'sql', PYTHON: 'python', SHELL: 'shell', SYNC: 'json' }
const TYPE_COLOR: Record<string, string> = { SQL: 'blue', PYTHON: 'green', SHELL: 'orange', SYNC: 'purple', VIRTUAL: 'default' }

/** 按工作区记住上次打开的脚本，下次进入数据开发自动打开（避免默认黑屏空编辑器） */
const LAST_STUDIO_NODE_KEY = 'gido.studio.lastNodeByWorkspace'

function readLastStudioNodeId(workspaceId: number | undefined): number | null {
  if (workspaceId == null) return null
  try {
    const raw = localStorage.getItem(LAST_STUDIO_NODE_KEY)
    if (!raw) return null
    const map = JSON.parse(raw) as Record<string, number>
    const id = map[String(workspaceId)]
    return typeof id === 'number' && Number.isFinite(id) ? id : null
  } catch {
    return null
  }
}

function writeLastStudioNodeId(workspaceId: number, nodeId: number) {
  try {
    const raw = localStorage.getItem(LAST_STUDIO_NODE_KEY)
    const map: Record<string, number> = raw ? JSON.parse(raw) : {}
    map[String(workspaceId)] = nodeId
    localStorage.setItem(LAST_STUDIO_NODE_KEY, JSON.stringify(map))
  } catch {
    /* ignore quota / private mode */
  }
}

const STUDIO_RESULT_COL_META = 'gido.studio.resultTableMeta.v1'

type StudioResultColMeta = { order: string[]; widths: Record<string, number> }

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
  return [...list].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
  )
}

function sameFolder(a: number | null | undefined, b: number | null | undefined): boolean {
  return (a ?? null) === (b ?? null)
}

export default function StudioPage() {
  const { currentWorkspace, pendingOpenNodeId, setPendingOpenNodeId, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)

  // 节点列表
  const [nodes, setNodes] = useState<any[]>([])
  const [folders, setFolders] = useState<any[]>([])
  const [datasources, setDatasources] = useState<any[]>([])

  // 编辑器 ref（用于格式化）
  const editorRef = useRef<any>(null)
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())
  const [openTabs, setOpenTabs] = useState<any[]>([])
  const [activeTabId, setActiveTabId] = useState<number | null>(null)

  // 编辑器内容（按 nodeId 存储，未保存的修改）
  const [dirtyMap, setDirtyMap] = useState<Record<number, string>>({})

  // 运行状态
  const [runningId, setRunningId] = useState<number | null>(null)
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

  // 节点配置抽屉
  const [configModal, setConfigModal] = useState(false)
  const [configForm] = Form.useForm()
  const [historyModal, setHistoryModal] = useState(false)
  const [historyList, setHistoryList] = useState<any[]>([])
  const [integrationTasks, setIntegrationTasks] = useState<any[]>([])
  /** 当前用户是否持有各节点的协作编辑锁（与发布锁定 is_locked 独立） */
  const [editLockHeld, setEditLockHeld] = useState<Record<number, boolean>>({})
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set())
  const [approvalNote, setApprovalNote] = useState('')
  const [approvalModalOpen, setApprovalModalOpen] = useState(false)

  const load = async () => {
    if (!wsId) return
    const [n, d, f, pendingRes]: any = await Promise.all([
      studioApi.listNodes(wsId),
      datasourceApi.list(wsId),
      studioApi.listFolders(wsId),
      approvalApi.list(wsId, { status: 'pending', page_size: 200 }),
    ])
    setNodes(sortNodesList(n as unknown as any[]))
    setDatasources(d as unknown as any[])
    setFolders(f as unknown as any[])
    setPendingKeys(
      new Set((pendingRes?.items || []).map((i: any) => `${i.resource_type}:${i.resource_id}:${i.action}`)),
    )
  }

  useEffect(() => { load() }, [wsId])

  useEffect(() => {
    if (!wsId) return
    integrationApi.listTasks(wsId).then((t: any) => setIntegrationTasks(Array.isArray(t) ? t : [])).catch(() => setIntegrationTasks([]))
  }, [wsId])

  const openNode = useCallback((node: any) => {
    setOpenTabs(prev => (prev.find(t => t.id === node.id) ? prev : [...prev, node]))
    setActiveTabId(node.id)
    setLogPanelOpen(false)
  }, [])

  const prevWsIdRef = useRef<number | undefined>(undefined)
  const studioRestoreDoneRef = useRef(false)
  useEffect(() => {
    if (prevWsIdRef.current !== undefined && prevWsIdRef.current !== wsId) {
      setOpenTabs([])
      setActiveTabId(null)
      setDirtyMap({})
      setEditLockHeld({})
      setLogPanelOpen(false)
      setRunningId(null)
      studioRestoreDoneRef.current = false
    }
    prevWsIdRef.current = wsId
  }, [wsId])

  // 工作流跳转优先；否则进入页面只尝试一次「恢复上次打开的脚本」（与 pending 同一轮逻辑，避免 openTabs 尚未提交时的竞态）
  useEffect(() => {
    if (!wsId || nodes.length === 0) return
    if (studioRestoreDoneRef.current) return

    if (pendingOpenNodeId != null) {
      const node = nodes.find(n => n.id === pendingOpenNodeId)
      setPendingOpenNodeId(null)
      if (node) {
        openNode(node)
        studioRestoreDoneRef.current = true
        return
      }
      // 节点已删或不存在：继续走下方「上次脚本」恢复
    }

    if (openTabs.length > 0) {
      studioRestoreDoneRef.current = true
      return
    }

    studioRestoreDoneRef.current = true
    const lastId = readLastStudioNodeId(wsId)
    if (lastId == null) return
    const node = nodes.find(n => n.id === lastId)
    if (node) openNode(node)
  }, [wsId, nodes, pendingOpenNodeId, openTabs.length, setPendingOpenNodeId, openNode])

  useEffect(() => {
    if (wsId != null && activeTabId != null) {
      writeLastStudioNodeId(wsId, activeTabId)
    }
  }, [wsId, activeTabId])

  /** 与 editLockHeld 同步，供 effect / 事件里读取最新占锁状态 */
  const editLockHeldRef = useRef(editLockHeld)
  editLockHeldRef.current = editLockHeld
  const activeTabIdRef = useRef(activeTabId)
  activeTabIdRef.current = activeTabId

  /** 切换 Tab 时释放上一节点由本会话占用的编辑锁，避免长期占用不编辑的节点 */
  const prevActiveTabIdRef = useRef<number | null>(null)
  useEffect(() => {
    const prev = prevActiveTabIdRef.current
    prevActiveTabIdRef.current = activeTabId
    if (prev != null && prev !== activeTabId && editLockHeldRef.current[prev] === true) {
      studioApi.releaseEditLock(prev).catch(() => {})
      setEditLockHeld(p => {
        const n = { ...p }
        delete n[prev]
        return n
      })
    }
  }, [activeTabId])

  // 关闭 tab
  const closeTab = (nodeId: number) => {
    if (editLockHeld[nodeId]) {
      studioApi.releaseEditLock(nodeId).catch(() => {})
      setEditLockHeld(prev => {
        const n = { ...prev }
        delete n[nodeId]
        return n
      })
    }
    const idx = openTabs.findIndex(t => t.id === nodeId)
    const newTabs = openTabs.filter(t => t.id !== nodeId)
    setOpenTabs(newTabs)
    if (activeTabId === nodeId) {
      setActiveTabId(newTabs[Math.max(0, idx - 1)]?.id ?? null)
    }
    // 清理 dirty
    setDirtyMap(prev => { const n = { ...prev }; delete n[nodeId]; return n })
  }

  // 当前激活节点
  const activeNode = openTabs.find(t => t.id === activeTabId)
  const activeScript = activeTabId !== null
    ? (dirtyMap[activeTabId] ?? activeNode?.script_content ?? '')
    : ''
  const holdsEditLock = activeTabId !== null && editLockHeld[activeTabId] === true
  const canEdit = Boolean(activeNode && !activeNode.is_locked && holdsEditLock)

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
    setResultColMeta({
      order: mergeColumnOrderWithKeys(stored.order, cols),
      widths: pruneWidths(stored.widths, cols),
    })
  }, [activeTabId, resultColSig])

  const onResultColumnOrderChange = useCallback(
    (nextOrder: string[]) => {
      if (activeTabId == null) return
      setResultColMeta(prev => {
        const next = { ...prev, order: nextOrder }
        saveStudioResultMetaNode(activeTabId, next)
        return next
      })
    },
    [activeTabId],
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

  /** 协作编辑锁：仅在用户点击/聚焦脚本区或显式保存等写操作时再占用，避免一进页面就弹 409 */
  const acquireLockPromiseRef = useRef<Promise<boolean> | null>(null)
  const requestEditLockOnInteraction = useCallback(
    async (opts?: { silent?: boolean }): Promise<boolean> => {
      const silent = opts?.silent ?? false
      if (activeTabId == null || !activeNode || activeNode.is_locked) return false
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
    [activeTabId, activeNode],
  )

  const handleEditorAreaPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return
      if (activeTabId == null || !activeNode || activeNode.is_locked) return
      if (editLockHeldRef.current[activeTabId] === true) return
      void requestEditLockOnInteraction()
    },
    [activeTabId, activeNode, requestEditLockOnInteraction],
  )

  const handleEditorAreaFocusCapture = useCallback(() => {
    if (activeTabId == null || !activeNode || activeNode.is_locked) return
    if (editLockHeldRef.current[activeTabId] === true) return
    void requestEditLockOnInteraction()
  }, [activeTabId, activeNode, requestEditLockOnInteraction])

  // 编辑器内容变化
  const onEditorChange = (val: string | undefined) => {
    if (activeTabId === null || !canEdit) return
    setDirtyMap(prev => ({ ...prev, [activeTabId]: val ?? '' }))
  }

  // 保存
  const handleSave = async (): Promise<boolean> => {
    if (!activeNode) return false
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
      updated = await studioApi.updateNode(activeNode.id, { ...activeNode, script_content: script, workspace_id: wsId })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
      return false
    }
    const nu = updated as any
    setNodes(prev => prev.map(n => n.id === activeNode.id ? { ...n, ...nu, script_content: script } : n))
    setOpenTabs(prev => prev.map(t => t.id === activeNode.id ? { ...t, ...nu, script_content: script } : t))
    setDirtyMap(prev => { const n = { ...prev }; delete n[activeNode.id]; return n })
    message.success('保存成功')
    return true
  }

  // 运行（直接用编辑器最新内容，不需要先保存）
  const dsResolve = useMemo(() => {
    if (!activeNode || activeNode.node_type !== 'SQL') return null
    return resolveDatasourceForRun(
      activeNode.datasource_id,
      currentWorkspace,
      datasources,
    )
  }, [activeNode, currentWorkspace, datasources])

  const handleRun = async () => {
    if (!activeNode) return
    if (activeNode.node_type === 'SQL' && !dsResolve?.effectiveId) {
      message.warning('请先在「空间设置」配置默认数据源，或在节点「配置」中单独指定')
      return
    }
    const latestScript = dirtyMap[activeTabId!] ?? activeNode.script_content ?? ''
    setRunningId(activeNode.id)
    setLogMap(prev => ({ ...prev, [activeNode.id]: '' }))
    setResultMap(prev => ({ ...prev, [activeNode.id]: null }))
    setLogPanelOpen(true)
    setResultTab(prev => ({ ...prev, [activeNode.id]: activeNode.node_type === 'SQL' ? 'result' : 'log' }))
    try {
      const res: any = await studioApi.runNode(activeNode.id, latestScript)
      setLogMap(prev => ({ ...prev, [activeNode.id]: res.log || '执行完成，无输出' }))
      if (res.result) setResultMap(prev => ({ ...prev, [activeNode.id]: res.result }))
    } catch (e: any) {
      setLogMap(prev => ({ ...prev, [activeNode.id]: e?.response?.data?.detail || '执行失败' }))
      setResultTab(prev => ({ ...prev, [activeNode.id]: 'log' }))
    }
    setRunningId(null)
  }

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

  // 重命名目录
  const [renamingFolderId, setRenamingFolderId] = useState<number | null>(null)
  const [renamingFolderName, setRenamingFolderName] = useState('')

  // 重命名节点
  const [renamingNodeId, setRenamingNodeId] = useState<number | null>(null)
  const [renamingNodeName, setRenamingNodeName] = useState('')

  // SQL 格式化
  const handleFormat = async () => {
    if (!activeNode || activeNode.node_type !== 'SQL') return
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
    try {
      const formatted = sqlFormat(current, { language: 'mysql', tabWidth: 2, keywordCase: 'upper' })
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

  // 重命名文件夹
  const handleRenameFolder = async (folderId: number) => {
    if (!renamingFolderName.trim()) return
    await studioApi.renameFolder(folderId, renamingFolderName.trim())
    setRenamingFolderId(null)
    setRenamingFolderName('')
    await load()
  }

  // 重命名节点
  const handleRenameNode = async (nodeId: number) => {
    if (!renamingNodeName.trim()) return
    const node = nodes.find(n => n.id === nodeId)
    if (!node) return
    if (node.is_locked) {
      message.warning('脚本已锁定，请先解锁后再重命名')
      setRenamingNodeId(null)
      return
    }
    try {
      await studioApi.updateNode(nodeId, { ...node, name: renamingNodeName.trim(), workspace_id: wsId })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '重命名失败')
      setRenamingNodeId(null)
      return
    }
    setNodes(prev => prev.map(n => n.id === nodeId ? { ...n, name: renamingNodeName.trim() } : n))
    setOpenTabs(prev => prev.map(t => t.id === nodeId ? { ...t, name: renamingNodeName.trim() } : t))
    setRenamingNodeId(null)
    setRenamingNodeName('')
  }

  // 新建节点
  const handleCreate = async () => {
    const values = await createForm.validateFields()
    values.workspace_id = wsId
    values.folder_id = createFolderId
    values.script_content = values.node_type === 'SQL'
      ? '-- 在此编写 SQL\nSELECT 1'
      : values.node_type === 'PYTHON'
        ? '# 在此编写 Python\nprint("hello gido")'
        : values.node_type === 'SYNC'
          ? '{"sync_task_id": null}'
          : '#!/bin/bash\necho "hello gido"'
    if (values.node_type === 'SYNC') {
      values.params = { sync_task_id: null }
    }
    if (values.node_type === 'SQL' && !values.datasource_id) {
      delete values.datasource_id
    }
    const node: any = await studioApi.createNode(values)
    setCreateModal(false)
    createForm.resetFields()
    setCreateFolderId(null)
    await load()
    openNode(node)
    message.success('创建成功')
  }

  // 打开节点配置（params 在接口里可能是 object；部分库/驱动也可能是 JSON 字符串，不能当成「无」而清空）
  const openConfig = () => {
    if (!activeNode) return
    const vals = { ...activeNode }
    const p = vals.params
    if (p == null || p === '') {
      vals.params = ''
    } else if (typeof p === 'object' && !Array.isArray(p)) {
      vals.params = JSON.stringify(p, null, 2)
    } else if (typeof p === 'string') {
      vals.params = p
    } else {
      vals.params = String(p)
    }
    if (vals.node_type === 'SYNC' && typeof vals.params === 'object' && vals.params?.sync_task_id != null) {
      vals.sync_task_id = vals.params.sync_task_id
    } else if (vals.node_type === 'SYNC' && typeof vals.params === 'string') {
      try {
        const o = JSON.parse(vals.params)
        vals.sync_task_id = o.sync_task_id
      } catch { /* ignore */ }
    }
    configForm.setFieldsValue(vals)
    setConfigModal(true)
  }

  const handleSaveConfig = async () => {
    if (!activeNode) return
    if (activeNode.is_locked) {
      message.warning('脚本已锁定，无法修改配置')
      return
    }
    let ok = activeTabId != null && editLockHeld[activeTabId] === true
    if (!ok) {
      ok = await requestEditLockOnInteraction({ silent: true })
    }
    if (!ok) {
      message.warning('请先点击脚本编辑区获取编辑锁后再保存配置；若当前由他人占用请使用「抢锁编辑」')
      return
    }
    const values = await configForm.validateFields()
    const raw = values.params
    if (raw === undefined || raw === null) {
      values.params = null
    } else if (typeof raw === 'string') {
      const s = raw.trim()
      if (s === '') {
        values.params = null
      } else {
        try {
          const parsed = JSON.parse(s)
          if (parsed !== null && (typeof parsed !== 'object' || Array.isArray(parsed))) {
            message.error('自定义变量须为键值对对象 {...}，不能是数组或纯字符串')
            return
          }
          values.params = parsed
        } catch {
          // 如 {'xx':'yy'} 非标准 JSON，交给后端用 ast.literal_eval 解析
          values.params = s
        }
      }
    }
    if (values.timeout_seconds === '' || values.timeout_seconds === undefined) {
      values.timeout_seconds = null
    }
    if (values.retry_times === '' || values.retry_times === undefined) {
      values.retry_times = null
    }
    if (activeNode.node_type === 'SYNC') {
      if (!values.sync_task_id) {
        message.error('请选择要绑定的数据集成任务')
        return
      }
      values.params = { sync_task_id: values.sync_task_id }
      delete values.sync_task_id
    }
    if (activeNode.node_type === 'SQL') {
      values.datasource_id = values.datasource_id ?? null
    }
    try {
      const updated: any = await studioApi.updateNode(activeNode.id, { ...activeNode, ...values, workspace_id: wsId })
      setNodes(prev => prev.map(n => (n.id === activeNode.id ? { ...n, ...updated } : n)))
      setOpenTabs(prev => prev.map(t => (t.id === activeNode.id ? { ...t, ...updated } : t)))
      setConfigModal(false)
      message.success('配置已保存')
    } catch (e: any) {
      const d = e?.response?.data?.detail
      const msg = Array.isArray(d)
        ? d.map((x: any) => x.msg || JSON.stringify(x)).join('; ')
        : (typeof d === 'string' ? d : e?.message || '保存失败')
      message.error(msg)
    }
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

  const onTreeDrop = async (info: any) => {
    const dragKey = info.dragNode.key
    const nodeId = typeof dragKey === 'number' ? dragKey : null
    if (nodeId == null || !wsId) return

    const dragged = nodes.find(n => n.id === nodeId)
    if (!dragged) return

    const dropKey = info.node.key
    let targetFolderId: number | null = null
    let relativeToNodeId: number | null = null
    let position: 'before' | 'after' = 'after'

    if (typeof dropKey === 'string' && dropKey.startsWith('folder-')) {
      targetFolderId = parseInt(dropKey.replace('folder-', ''), 10)
    } else if (dropKey === 'root') {
      targetFolderId = null
    } else if (typeof dropKey === 'number') {
      const tgt = nodes.find(n => n.id === dropKey)
      if (!tgt) return
      targetFolderId = tgt.folder_id ?? null
      relativeToNodeId = dropKey
      if (info.dropToGap) {
        const dropPos = info.node.pos.split('-')
        const dropPosition = info.dropPosition - Number(dropPos[dropPos.length - 1])
        position = dropPosition <= 0 ? 'before' : 'after'
      } else {
        position = 'after'
      }
    } else {
      return
    }

    const folderPeers = sortNodesList(
      nodes.filter(n => sameFolder(n.folder_id, targetFolderId) && n.id !== nodeId),
    )
    let orderedIds = folderPeers.map(n => n.id)
    if (relativeToNodeId != null) {
      const idx = orderedIds.indexOf(relativeToNodeId)
      const insertAt = idx < 0 ? orderedIds.length : (position === 'before' ? idx : idx + 1)
      orderedIds.splice(insertAt, 0, nodeId)
    } else {
      orderedIds.push(nodeId)
    }

    const folderChanged = !sameFolder(dragged.folder_id, targetFolderId)
    if (
      !folderChanged
      && relativeToNodeId == null
      && sameFolder(dragged.folder_id, targetFolderId)
    ) {
      return
    }
    if (
      !folderChanged
      && relativeToNodeId != null
      && orderedIds.join(',') === sortNodesList(nodes.filter(n => sameFolder(n.folder_id, targetFolderId))).map(n => n.id).join(',')
    ) {
      return
    }

    try {
      if (folderChanged) {
        await studioApi.moveNodeFolder(nodeId, targetFolderId)
      }
      await studioApi.reorderNodes(wsId, targetFolderId, orderedIds)
      message.success(folderChanged ? '已移动并更新顺序' : '已更新顺序')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '移动失败')
    }
  }

  // 构建目录树
  const buildTree = () => {
    const folderMap: Record<number, any> = {}
    folders.forEach(f => {
      folderMap[f.id] = {
        key: `folder-${f.id}`,
        title: renamingFolderId === f.id ? (
          <Input
            size="small"
            autoFocus
            defaultValue={f.name}
            style={{ width: 120 }}
            onChange={e => setRenamingFolderName(e.target.value)}
            onPressEnter={() => handleRenameFolder(f.id)}
            onBlur={() => handleRenameFolder(f.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={() => { setRenamingFolderId(f.id); setRenamingFolderName(f.name) }}
          >
            <span><FolderOutlined style={{ marginRight: 6, color: '#faad14' }} />{f.name}</span>
            <Dropdown menu={{ items: [
              { key: 'add-node', label: '新建节点', onClick: () => { setCreateFolderId(f.id); setCreateModal(true) } },
              { key: 'add-folder', label: '新建子目录', onClick: () => { setFolderParentId(f.id); setFolderModal(true) } },
              { key: 'rename', label: '重命名', onClick: () => { setRenamingFolderId(f.id); setRenamingFolderName(f.name) } },
              { key: 'delete', label: <span style={{ color: 'red' }}>删除目录</span>, onClick: () => handleDeleteFolder(f.id) },
            ]}} trigger={['click']}>
              <MoreOutlined style={{ padding: '0 4px', color: '#999' }} onClick={e => e.stopPropagation()} />
            </Dropdown>
          </div>
        ),
        children: [],
        isLeaf: false,
        _folderId: f.id,
        _parentId: f.parent_id,
      }
    })
    // 节点挂在对应文件夹下（按 sort_order 排序）
    const rootNodes: any[] = []
    sortNodesList(nodes).forEach(n => {
      const nodeItem = {
        key: n.id,
        title: renamingNodeId === n.id ? (
          <Input
            size="small"
            autoFocus
            defaultValue={n.name}
            style={{ width: 130 }}
            onChange={e => setRenamingNodeName(e.target.value)}
            onPressEnter={() => handleRenameNode(n.id)}
            onBlur={() => handleRenameNode(n.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={() => { setRenamingNodeId(n.id); setRenamingNodeName(n.name) }}
          >
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <FileOutlined style={{ marginRight: 6, color: '#1677ff' }} />{n.name}
            </span>
            <Dropdown menu={{ items: [
              { key: 'open', label: '打开', onClick: () => openNode(n) },
              { key: 'rename', label: '重命名', onClick: () => { setRenamingNodeId(n.id); setRenamingNodeName(n.name) } },
              { key: 'delete', label: <span style={{ color: 'red' }}>删除</span>, onClick: () => handleDelete(n.id) },
            ]}} trigger={['click']}>
              <MoreOutlined style={{ padding: '0 4px', color: '#999' }} onClick={e => e.stopPropagation()} />
            </Dropdown>
          </div>
        ),
        isLeaf: true,
        data: n,
      }
      if (n.folder_id && folderMap[n.folder_id]) {
        folderMap[n.folder_id].children.push(nodeItem)
      } else {
        rootNodes.push(nodeItem)
      }
    })
    // 构建文件夹层级结构
    const rootFolders: any[] = []
    Object.values(folderMap).forEach((f: any) => {
      f.children = (f.children || []).filter((c: any) => c.isLeaf !== false || c._folderId != null)
      const leafChildren = f.children.filter((c: any) => c.isLeaf)
      const subFolders = f.children.filter((c: any) => !c.isLeaf)
      leafChildren.sort(
        (a: any, b: any) => (a.data?.sort_order ?? 0) - (b.data?.sort_order ?? 0) || a.data.id - b.data.id,
      )
      f.children = [...subFolders, ...leafChildren]
      if (f._parentId && folderMap[f._parentId]) {
        folderMap[f._parentId].children.unshift(f)
      } else {
        rootFolders.push(f)
      }
    })
    return [
      {
        key: 'root',
        title: (
          <span style={{ fontWeight: 600 }}>
            <FolderOutlined style={{ marginRight: 6 }} />节点列表
          </span>
        ),
        children: [...rootFolders, ...rootNodes],
      }
    ]
  }

  const isRunning = activeTabId !== null && runningId === activeTabId
  const isDirty = activeTabId !== null && dirtyMap[activeTabId] !== undefined

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
    return (
      <Editor
        key={activeTabId ?? 0}
        height="100%"
        language={LANG_MAP[activeNode!.node_type] || 'plaintext'}
        value={activeScript}
        onChange={!canEdit ? undefined : onEditorChange}
        beforeMount={registerDwMonacoThemes}
        onMount={(editor) => { editorRef.current = editor }}
        theme={editorAppearance.theme}
        options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), readOnly: Boolean(!canEdit) }}
      />
    )
  }

  const editorCaptureProps = activeNode?.node_type === 'SYNC'
    ? {}
    : { onPointerDownCapture: handleEditorAreaPointerDown, onFocusCapture: handleEditorAreaFocusCapture }

  return (
    <>
    <ResizableSidebar
      storageKey="gido.studio.sidebarWidth"
      defaultWidth={240}
      minWidth={180}
      maxWidth={560}
      style={{ height: 'calc(100vh - 112px)', margin: -24, overflow: 'hidden' }}
      left={(
      <div style={{ display: 'flex', flexDirection: 'column', background: '#fafafa', height: '100%', minHeight: 0 }}>
        <div style={{ padding: '10px 12px', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontWeight: 600, fontSize: 13 }}>{BRAND.offline}</span>
          <Space size={0}>
            <Tooltip title="新建目录">
              <Button type="text" size="small" icon={<FolderAddOutlined />} onClick={() => { setFolderParentId(null); setFolderModal(true) }} />
            </Tooltip>
            <Tooltip title="新建节点">
              <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => { setCreateFolderId(null); setCreateModal(true) }} />
            </Tooltip>
          </Space>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
          <Tree
            treeData={buildTree()}
            defaultExpandAll
            blockNode
            draggable
            onDrop={onTreeDrop}
            selectedKeys={activeTabId ? [activeTabId] : []}
            onSelect={(keys, { node }: any) => {
              if (node.data) openNode(node.data)
            }}
            style={{ background: 'transparent' }}
          />
        </div>
      </div>
      )}
      right={(
      <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Tab 栏 */}
        <div style={{ borderBottom: '1px solid #f0f0f0', background: '#fff', display: 'flex', alignItems: 'center', minHeight: 40, overflowX: 'auto' }}>
          {openTabs.map(tab => {
            const dirty = dirtyMap[tab.id] !== undefined
            const isActive = tab.id === activeTabId
            return (
              <div
                key={tab.id}
                onClick={() => setActiveTabId(tab.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '0 14px', height: 40, cursor: 'pointer', whiteSpace: 'nowrap',
                  borderRight: '1px solid #f0f0f0',
                  borderBottom: isActive ? '2px solid #1677ff' : '2px solid transparent',
                  background: isActive ? '#fff' : '#fafafa',
                  color: isActive ? '#1677ff' : '#666',
                  fontSize: 13,
                }}
              >
                <Tag color={TYPE_COLOR[tab.node_type]} style={{ margin: 0, fontSize: 11 }}>{tab.node_type}</Tag>
                <span>{tab.name}</span>
                {dirty && <span style={{ color: '#faad14', fontSize: 10 }}>●</span>}
                <CloseCircleOutlined
                  style={{ fontSize: 12, color: '#999', marginLeft: 2 }}
                  onClick={e => { e.stopPropagation(); closeTab(tab.id) }}
                />
              </div>
            )
          })}
          {openTabs.length === 0 && (
            <span style={{ padding: '0 16px', color: '#bbb', fontSize: 13 }}>双击左侧节点打开编辑</span>
          )}
        </div>

        {activeNode ? (
          <>
            {/* 工具栏 */}
            <div style={{ padding: '6px 12px', borderBottom: '1px solid #f0f0f0', background: '#fff', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Button
                type="primary"
                icon={isRunning ? <LoadingOutlined /> : <PlayCircleOutlined />}
                onClick={handleRun}
                disabled={isRunning}
                size="small"
              >
                {isRunning ? '运行中...' : '运行'}
              </Button>
              <Button
                icon={<SaveOutlined />}
                onClick={handleSave}
                size="small"
                type={isDirty ? 'default' : 'text'}
                disabled={activeNode.is_locked}
              >
                保存{isDirty ? ' *' : ''}
              </Button>
              {activeNode?.node_type === 'SQL' && dsResolve && (
                <>
                  <Button icon={<FormatPainterOutlined />} onClick={() => void handleFormat()} size="small" disabled={activeNode.is_locked}>格式化</Button>
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
                </>
              )}
              <Button
                icon={<CloudUploadOutlined />}
                onClick={handlePublish}
                size="small"
                title={canPublishDirect ? '提交后锁定脚本，需解锁再改' : '提交审批，管理员通过后锁定脚本'}
                disabled={activeNode.is_locked || isNodePendingApproval}
              >
                {isNodePendingApproval ? '审批中' : canPublishDirect ? '提交' : '提交审批'}
              </Button>
              {activeNode.is_locked && (
                <Button icon={<UnlockOutlined />} size="small" onClick={handleUnlock}>
                  解锁
                </Button>
              )}
              {!canEdit && !activeNode.is_locked && activeNode.edit_lock_username && (
                <Button size="small" danger icon={<LockOutlined />} onClick={handleStealEditLock}>抢锁编辑</Button>
              )}
              <Button icon={<SettingOutlined />} onClick={openConfig} size="small">配置</Button>
              <Button icon={<ReloadOutlined />} onClick={openHistory} size="small">版本历史</Button>
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
            </div>

            {/* 编辑器 + 日志面板（打开时中间横条可拖拽调整上下高度） */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
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
                    <div
                      style={{
                        background: '#fafafa',
                        display: 'flex',
                        flexDirection: 'column',
                        height: '100%',
                        minHeight: 0,
                        overflow: 'hidden',
                      }}
                    >
                      {/* 面板标题栏 */}
                      <div
                        style={{
                          padding: '0 12px',
                          background: '#fff',
                          borderBottom: '1px solid #f0f0f0',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 0,
                          minHeight: 40,
                        }}
                      >
                        <div
                          onClick={() => setResultTab(prev => ({ ...prev, [activeTabId!]: 'log' }))}
                          style={{
                            padding: '0 14px', height: 40, lineHeight: '40px', cursor: 'pointer', fontSize: 13,
                            color: (resultTab[activeTabId!] ?? 'log') === 'log' ? '#1677ff' : '#666',
                            fontWeight: (resultTab[activeTabId!] ?? 'log') === 'log' ? 600 : 400,
                            borderBottom: (resultTab[activeTabId!] ?? 'log') === 'log' ? '2px solid #1677ff' : '2px solid transparent',
                          }}
                        >
                          日志 {isRunning && <Spin size="small" style={{ marginLeft: 6 }} />}
                        </div>
                        <div
                          onClick={() => setResultTab(prev => ({ ...prev, [activeTabId!]: 'result' }))}
                          style={{
                            padding: '0 14px', height: 40, lineHeight: '40px', cursor: 'pointer', fontSize: 13,
                            color: (resultTab[activeTabId!] ?? 'log') === 'result' ? '#1677ff' : '#666',
                            fontWeight: (resultTab[activeTabId!] ?? 'log') === 'result' ? 600 : 400,
                            borderBottom: (resultTab[activeTabId!] ?? 'log') === 'result' ? '2px solid #1677ff' : '2px solid transparent',
                          }}
                        >
                          查询结果
                          {resultMap[activeTabId!] && (
                            <span style={{ marginLeft: 8, color: '#52c41a', fontSize: 12, fontWeight: 400 }}>
                              {resultMap[activeTabId!]!.total} 行
                            </span>
                          )}
                        </div>
                        <div style={{ flex: 1 }} />
                        <Button
                          type="text" size="small"
                          icon={<CloseCircleOutlined />}
                          style={{ color: '#999' }}
                          onClick={() => setLogPanelOpen(false)}
                        />
                      </div>

                      {(resultTab[activeTabId!] ?? 'log') === 'log' && (
                        <pre style={{
                          flex: 1, margin: 0, padding: '10px 14px',
                          color: '#333', fontSize: 13, overflow: 'auto',
                          whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, monospace',
                          background: '#fff',
                        }}>
                          {isRunning ? '执行中...' : (logMap[activeTabId!] || '暂无日志')}
                        </pre>
                      )}

                      {(resultTab[activeTabId!] ?? 'log') === 'result' && (
                        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column', background: '#fff' }}>
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
                                  toolbar={(
                                    <div style={{ padding: '6px 12px', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                                      <span style={{ color: '#666', fontSize: 12 }}>
                                        共 <strong>{total}</strong> 行；展示 <strong>{rows.length}</strong> 行（上限 10000）；表头右上角为类型徽章（悬停看完整类型）
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
                      )}
                    </div>
                  )}
                />
              ) : (
                <div style={{ flex: 1, overflow: 'hidden', minHeight: 0 }} {...editorCaptureProps}>
                  {renderScriptPane()}
                </div>
              )}
            </div>
          </>
        ) : (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12, color: '#666', background: '#fafafa' }}>
            <FileOutlined style={{ fontSize: 48, color: '#bfbfbf' }} />
            <p style={{ fontSize: 14 }}>从左侧双击节点打开脚本，或新建节点</p>
            <Button icon={<PlusOutlined />} onClick={() => setCreateModal(true)}>新建节点</Button>
          </div>
        )}
      </div>
      )}
    />

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
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ color: '#999', fontSize: 12 }}>{h.saved_at}</span>
              <Button size="small" onClick={() => handleRollback(h.id, h.script_content)}>回滚到此版本</Button>
            </div>
            <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 12, maxHeight: 120, overflow: 'auto', margin: 0 }}>
              {h.script_content?.slice(0, 300)}{h.script_content?.length > 300 ? '...' : ''}
            </pre>
          </div>
        ))}
      </Modal>

      {/* 节点配置弹窗 */}
      <Modal title="节点配置" open={configModal} onOk={handleSaveConfig} onCancel={() => setConfigModal(false)} width={480}>
        <Form form={configForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="节点名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {activeNode?.node_type === 'SQL' && (
            <Form.Item
              name="datasource_id"
              label="数据源（可选）"
              extra={
                currentWorkspace?.default_datasource_id
                  ? '不选则继承空间默认；选定后该节点固定此数据源（不随空间默认变更）'
                  : '请先在「空间设置」配置默认数据源'
              }
            >
              <Select
                allowClear
                placeholder={
                  dsResolve?.source === 'workspace' && dsResolve.effective
                    ? `继承空间默认：${dsResolve.effective.name}`
                    : '继承空间默认'
                }
                options={datasources.map((d: any) => ({ label: `${d.name} (${d.ds_type})`, value: d.id }))}
              />
            </Form.Item>
          )}
          {activeNode?.node_type === 'SYNC' && (
            <Form.Item name="sync_task_id" label="绑定的数据集成任务" rules={[{ required: true }]}>
              <Select
                placeholder="选择同步任务"
                options={integrationTasks.map((t: any) => ({
                  label: `${t.name} (#${t.id}, ${t.sync_mode})`,
                  value: t.id,
                }))}
              />
            </Form.Item>
          )}
          <Form.Item name="timeout_seconds" label="超时时间（秒）">
            <Input type="number" />
          </Form.Item>
          <Form.Item name="retry_times" label="失败重试次数">
            <Input type="number" />
          </Form.Item>
          <Form.Item
            name="params"
            label="自定义变量（对象）"
            tooltip={'标准 JSON 用双引号；含 $[yyyy-MM-dd-1] 等时间宏的键会同步到 Dolphin 全局参数（值里任意位置有 $[...] 即可，如 {"xx":"$[yyyy-MM-dd-1]"} 或 {"xx":"xx:$[yyyy-MM-dd-1]"}）'}
          >
            <Input.TextArea rows={3} placeholder={'{"xx": "yy"}'} />
          </Form.Item>
          <div style={{ color: '#999', fontSize: 12, marginTop: -8 }}>
            调度配置在《工作流管理》中设置
          </div>
        </Form>
      </Modal>

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

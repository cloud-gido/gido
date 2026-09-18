/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef, type Key } from 'react'
import {
  Button, Select, Alert, message, Input, Modal, Form, Tooltip, Tag, Spin,
} from 'antd'
import {
  PlusOutlined, FolderAddOutlined,
  FormatPainterOutlined, MenuFoldOutlined, AimOutlined,
} from '@ant-design/icons'
import '../monacoSetup'
import Editor from '@monaco-editor/react'
import { format as sqlFormat } from 'sql-formatter'
import { probeApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableVerticalSplit from '../components/ResizableVerticalSplit'
import StudioWorkbenchShell, {
  StudioWorkbenchActiveEntityTitle,
  StudioWorkbenchExpandSidebarButton,
  StudioWorkbenchStage,
  StudioWorkbenchToolbar,
  StudioWorkbenchTopStrip,
} from '../components/StudioWorkbenchShell'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import MonacoFindBar, { bindMonacoFindKeybindings, type MonacoFindBarApi } from '../components/MonacoFindBar'
import { bindMonacoScriptKeybindings } from '../utils/monacoScriptKeybindings'
import { useSqlSchemaCompletion } from '../hooks/useSqlSchemaCompletion'
import { PROBE_DEFAULT_ROW_LIMIT, clampSqlResultRowLimit } from '../utils/sqlResultRowLimit'
import SqlRunWithRowLimitButton from '../components/SqlRunWithRowLimitButton'
import {
  datasourceTagText,
  hasExplicitDatasource,
  peekCachedDatasources,
  rememberDatasources,
  resolveDatasourceForRun,
} from '../utils/workspaceDatasource'
import {
  type ProbeWorkspaceState,
  type ProbeFolder,
  type ProbeScript,
  loadProbeState,
  saveProbeState,
  defaultProbeState,
  newProbeId,
  mergeLocalIntoRemote,
  initialProbeWorkspaceState,
  probeTreeReadyFromCache,
  uniqueProbeCopyName,
} from '../utils/probeLocalStore'
import WorkspaceFolderTree, { locateLeafInFolderTree, type FolderRow, type LeafRow } from '../components/WorkspaceFolderTree'
import AutosaveStatusHint from '../components/AutosaveStatusHint'
import { useScriptAutosave } from '../hooks/useScriptAutosave'
import { useInteractiveRun } from '../hooks/useInteractiveRun'
import InteractiveRunDock from '../components/InteractiveRunDock'

function sameParent(a: string | null | undefined, b: string | null | undefined) {
  return (a ?? null) === (b ?? null)
}

function sortOrderForNewFolder(folders: ProbeFolder[], parentId: string | null): number {
  const peers = folders.filter(f => sameParent(f.parentId, parentId))
  const orders = peers.map(f => f.sort_order ?? 0)
  if (!orders.some(o => o > 0)) return 0
  return Math.max(...orders) + 10
}

function sortOrderForNewScript(scripts: ProbeScript[], folderId: string | null): number {
  const peers = scripts.filter(s => sameParent(s.folderId, folderId))
  const orders = peers.map(s => s.sort_order ?? 0)
  if (!orders.some(o => o > 0)) return 0
  return Math.max(...orders) + 10
}

export default function ProbePage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const [datasources, setDatasources] = useState<any[]>(() =>
    peekCachedDatasources(useAppStore.getState().currentWorkspace?.id),
  )
  /** 有本地缓存时先同步灌入，避免主区先闪「加载探查目录…」而侧栏已像就绪 */
  const [probeState, setProbeState] = useState<ProbeWorkspaceState>(() =>
    initialProbeWorkspaceState(useAppStore.getState().currentWorkspace?.id),
  )
  const [treeReady, setTreeReady] = useState(() =>
    probeTreeReadyFromCache(useAppStore.getState().currentWorkspace?.id),
  )
  const [loading, setLoading] = useState(false)
  /** 与 Studio 一致：可关闭底部结果面板，再次运行时自动打开 */
  const [resultPanelOpen, setResultPanelOpen] = useState(false)
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())

  const [folderModal, setFolderModal] = useState(false)
  const [folderForm] = Form.useForm()
  const [folderParentId, setFolderParentId] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('gido.probe.sidebarCollapsed') === '1'
    } catch {
      return false
    }
  })
  const [treeExpandedKeys, setTreeExpandedKeys] = useState<Key[]>(['root'])
  const editorRef = useRef<any>(null)
  const findApiRef = useRef<MonacoFindBarApi | null>(null)
  const [sqlDirty, setSqlDirty] = useState(false)
  const probeStateRef = useRef(probeState)
  probeStateRef.current = probeState

  useLayoutEffect(() => {
    if (!wsId) return
    const local = loadProbeState(wsId)
    if (local) {
      setProbeState(local)
      setTreeReady(true)
    } else {
      // 无本地缓存才挡主区；有缓存则后台对齐远端，不闪全屏 Spin
      setTreeReady(probeTreeReadyFromCache(wsId))
    }
  }, [wsId])

  useEffect(() => {
    if (!wsId) return
    let cancelled = false
    const local = loadProbeState(wsId)
    ;(async () => {
      let next: ProbeWorkspaceState | null = null
      try {
        const remote: any = await probeApi.getTree(wsId)
        if (cancelled) return
        if (remote && Array.isArray(remote.scripts) && remote.scripts.length) {
          const remoteState: ProbeWorkspaceState = {
            folders: Array.isArray(remote.folders) ? remote.folders : [],
            scripts: remote.scripts,
            activeScriptId: remote.activeScriptId || remote.scripts[0].id,
          }
          if (local) {
            const { merged, changed } = mergeLocalIntoRemote(remoteState, local)
            next = merged
            if (changed) {
              await probeApi.saveTree({
                workspace_id: wsId,
                folders: merged.folders,
                scripts: merged.scripts,
                activeScriptId: merged.activeScriptId,
              }).catch(() => undefined)
            }
          } else {
            next = remoteState
          }
        } else if (local) {
          next = local
          await probeApi.saveTree({
            workspace_id: wsId,
            folders: local.folders,
            scripts: local.scripts,
            activeScriptId: local.activeScriptId,
          }).catch(() => undefined)
        }
      } catch {
        next = local
      }
      if (cancelled) return
      if (next) {
        setProbeState(next)
        saveProbeState(wsId, next)
      } else {
        const init = defaultProbeState()
        setProbeState(init)
        saveProbeState(wsId, init)
      }
      setTreeReady(true)
    })()
    return () => {
      cancelled = true
    }
  }, [wsId])

  const setSidebarCollapsedPersist = (collapsed: boolean) => {
    setSidebarCollapsed(collapsed)
    try {
      localStorage.setItem('gido.probe.sidebarCollapsed', collapsed ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (!wsId) return
    datasourceApi.list(wsId).then((d: any) => {
      const list = Array.isArray(d) ? d : []
      rememberDatasources(wsId, list)
      setDatasources(list)
    })
  }, [wsId])

  useEffect(() => {
    const pending = sessionStorage.getItem('gido_copilot_sql')
    if (!pending || !wsId) return
    sessionStorage.removeItem('gido_copilot_sql')
    setProbeState(prev => {
      const id = prev.activeScriptId
      if (!id) return prev
      const next = {
        ...prev,
        scripts: prev.scripts.map(s => (s.id === id ? { ...s, sql: pending } : s)),
      }
      saveProbeState(wsId, next)
      return next
    })
  }, [wsId])

  useEffect(() => {
    if (!wsId || !treeReady) return
    const t = window.setTimeout(() => {
      saveProbeState(wsId, probeState)
      void probeApi.saveTree({
        workspace_id: wsId,
        folders: probeState.folders,
        scripts: probeState.scripts,
        activeScriptId: probeState.activeScriptId,
      }).catch(() => undefined)
    }, 400)
    return () => window.clearTimeout(t)
  }, [wsId, probeState, treeReady])

  const activeScript = useMemo(
    () => probeState.scripts.find(s => s.id === probeState.activeScriptId) ?? null,
    [probeState.scripts, probeState.activeScriptId],
  )
  const interactiveRun = useInteractiveRun({
    workspaceId: wsId,
    source: 'probe',
    recoveryKey: activeScript?.id,
  })
  const lastRunErrorRef = useRef('')

  useEffect(() => {
    setLoading(interactiveRun.isActive)
    if (interactiveRun.error && interactiveRun.error !== lastRunErrorRef.current) {
      lastRunErrorRef.current = interactiveRun.error
      message.error(interactiveRun.error)
    }
  }, [interactiveRun.isActive, interactiveRun.error])

  const treeFolders = useMemo<FolderRow<string>[]>(
    () => probeState.folders.map(f => ({
      id: f.id,
      name: f.name,
      parent_id: f.parentId,
      sort_order: f.sort_order ?? 0,
    })),
    [probeState.folders],
  )

  const treeLeaves = useMemo<LeafRow<string>[]>(
    () => probeState.scripts.map(s => ({
      id: s.id,
      name: s.name,
      folder_id: s.folderId,
      leaf_type: 'SQL',
      sort_order: s.sort_order ?? 0,
    })),
    [probeState.scripts],
  )

  const locateActiveInTree = () => {
    const script = probeState.scripts.find(s => s.id === probeState.activeScriptId)
    if (!script) {
      message.info('请先打开一个查询')
      return
    }
    setSidebarCollapsedPersist(false)
    locateLeafInFolderTree({
      leafId: script.id,
      leaves: treeLeaves,
      folders: treeFolders,
      expandedKeys: treeExpandedKeys,
      setExpandedKeys: setTreeExpandedKeys,
      treeSelector: '.probe-script-tree',
    })
  }

  const patchActiveScript = useCallback((patch: Partial<ProbeScript>) => {
    setProbeState(prev => {
      const id = prev.activeScriptId
      if (!id) return prev
      return {
        ...prev,
        scripts: prev.scripts.map(s => (s.id === id ? { ...s, ...patch } : s)),
      }
    })
  }, [])

  const sql = activeScript?.sql ?? ''
  const limit = activeScript?.limit ?? PROBE_DEFAULT_ROW_LIMIT

  const activeScriptIdRef = useRef<string | null>(null)
  activeScriptIdRef.current = probeState.activeScriptId
  const sqlRef = useRef(sql)
  sqlRef.current = sql

  useEffect(() => {
    setSqlDirty(false)
  }, [probeState.activeScriptId])

  const scriptAutosave = useScriptAutosave({
    enabled: Boolean(wsId && activeScript),
    dirty: sqlDirty,
    value: sql,
    storageKey: null,
    entityId: activeScript?.id ?? null,
    persist: async () => {
      if (!wsId) throw new Error('no workspace')
      // 权威在整棵探查状态树；sql 已在 onChange 写入 scripts[]
      saveProbeState(wsId, probeStateRef.current)
      void probeApi.saveTree({
        workspace_id: wsId,
        folders: probeStateRef.current.folders,
        scripts: probeStateRef.current.scripts,
        activeScriptId: probeStateRef.current.activeScriptId,
      }).catch(() => undefined)
    },
    onSynced: (script, entityId) => {
      if (entityId == null) return
      if (activeScriptIdRef.current !== entityId) return
      if (sqlRef.current !== script) return
      setSqlDirty(false)
    },
  })

  const probeDsResolve = useMemo(() => {
    if (!activeScript) return null
    return resolveDatasourceForRun(activeScript.datasource_id, currentWorkspace, datasources)
  }, [activeScript, currentWorkspace, datasources])

  const probeDefaultCatalog = useMemo(() => {
    const id = probeDsResolve?.effectiveId
    if (id == null) return null
    const ds = datasources.find((d: any) => d.id === id)
    return (ds?.database || null) as string | null
  }, [probeDsResolve?.effectiveId, datasources])

  const { bindSqlSchemaCompletion } = useSqlSchemaCompletion({
    datasourceId: probeDsResolve?.effectiveId ?? null,
    defaultCatalog: probeDefaultCatalog,
  })

  useEffect(() => {
    if (!datasources.length || !activeScript) return
    if (!hasExplicitDatasource(activeScript.datasource_id)) return
    const valid = datasources.some((d: any) => d.id === activeScript.datasource_id)
    if (!valid) {
      message.warning('此查询绑定的数据源已删除，请重新在配置中选择或清空以继承空间默认')
      patchActiveScript({ datasource_id: undefined })
    }
  }, [datasources, activeScript?.id, activeScript?.datasource_id, patchActiveScript])

  const run = async (overrideSql?: string, meta?: { fromSelection?: boolean }) => {
    if (!wsId || !activeScript) {
      message.warning('请选择或新建一条探查查询')
      return
    }
    const runDs = probeDsResolve?.effectiveId
    if (!runDs) {
      message.warning('请先在「空间设置」配置默认数据源，或在本查询上单独选择数据源')
      return
    }
    const sqlToRun = overrideSql ?? activeScript.sql
    if (meta?.fromSelection) {
      message.info('已执行选中片段')
    }
    setLoading(true)
    lastRunErrorRef.current = ''
    setResultPanelOpen(true)
    try {
      const res: any = await interactiveRun.start(() => probeApi.submitRun({
        workspace_id: wsId,
        datasource_id: runDs,
        sql: sqlToRun,
        limit: activeScript.limit,
        client_key: activeScript.id,
      }))
      if (res?.reused) message.info('相同查询正在运行，已恢复进度')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '执行失败')
      setLoading(false)
    }
  }

  const runRef = useRef(run)
  runRef.current = run

  const bindProbeScriptKeys = (ed: any, monaco: any) => {
    bindMonacoFindKeybindings(ed, monaco, () => findApiRef.current)
    bindMonacoScriptKeybindings(ed, monaco, {
      enableRun: () => Boolean(activeScriptIdRef.current),
      onRun: (sql, meta) => {
        void runRef.current(sql, meta)
      },
    })
    bindSqlSchemaCompletion(ed, monaco)
  }

  const formatSql = () => {
    const raw = (activeScript?.sql || '').trim()
    if (!raw) return
    const dsType = String(
      datasources.find((d: any) => d.id === probeDsResolve?.effectiveId)?.ds_type || '',
    ).toLowerCase()
    const language = dsType === 'postgresql' || dsType === 'postgres' ? 'postgresql' : 'mysql'
    try {
      const formatted = sqlFormat(raw, { language, tabWidth: 2, keywordCase: 'upper' })
      patchActiveScript({ sql: formatted })
      message.success('已格式化 SQL')
    } catch (e: any) {
      message.error(e?.message || '格式化失败，请检查 SQL 语法')
    }
  }

  const addFolder = (parentId: string | null) => {
    setFolderParentId(parentId)
    folderForm.resetFields()
    setFolderModal(true)
  }

  const handleCreateFolder = async () => {
    const v = await folderForm.validateFields()
    const id = newProbeId('f')
    setProbeState(prev => ({
      ...prev,
      folders: [
        ...prev.folders,
        {
          id,
          name: v.name,
          parentId: folderParentId,
          sort_order: sortOrderForNewFolder(prev.folders, folderParentId),
        },
      ],
    }))
    setFolderModal(false)
    setFolderParentId(null)
    message.success('已新建目录')
  }

  const addScript = (folderId: string | null) => {
    const id = newProbeId('s')
    const n = probeState.scripts.length + 1
    setProbeState(prev => ({
      ...prev,
      scripts: [
        ...prev.scripts,
        {
          id,
          name: `新建查询_${n}`,
          folderId,
          sql: 'SELECT 1',
          limit: PROBE_DEFAULT_ROW_LIMIT,
          sort_order: sortOrderForNewScript(prev.scripts, folderId),
          // 新建查询不写入 datasource_id，运行期继承空间默认
        },
      ],
      activeScriptId: id,
    }))
    message.success('已新建查询')
  }

  const deleteScript = (id: string) => {
    if (probeState.scripts.length <= 1) {
      message.warning('至少保留一条探查查询')
      return
    }
    setProbeState(prev => {
      const scripts = prev.scripts.filter(s => s.id !== id)
      let activeScriptId = prev.activeScriptId
      if (activeScriptId === id) activeScriptId = scripts[0]?.id ?? null
      return { ...prev, scripts, activeScriptId }
    })
    message.success('已删除')
  }

  const copyScript = (leafId: string) => {
    const src = probeState.scripts.find(s => s.id === leafId)
    if (!src) return
    const id = newProbeId('s')
    const name = uniqueProbeCopyName(probeState.scripts.map(s => s.name), src.name)
    setProbeState(prev => ({
      ...prev,
      scripts: [
        ...prev.scripts,
        {
          id,
          name,
          folderId: src.folderId,
          sql: src.sql,
          limit: src.limit,
          datasource_id: src.datasource_id,
          sort_order: sortOrderForNewScript(prev.scripts, src.folderId),
        },
      ],
      activeScriptId: id,
    }))
    setResultPanelOpen(false)
    message.success(`已复制为「${name}」`)
  }

  const deleteFolder = async (folderId: string) => {
    const hasChildFolders = probeState.folders.some(f => f.parentId === folderId)
    if (hasChildFolders) {
      message.warning('请先删除或移出子目录后再删除（与数据开发/实时一致：仅空目录可删，叶子会移到根级）')
      return
    }
    setProbeState(prev => ({
      ...prev,
      folders: prev.folders.filter(f => f.id !== folderId),
      scripts: prev.scripts.map(s => (s.folderId === folderId ? { ...s, folderId: null } : s)),
    }))
    message.success('已删除目录（目录内查询已移到根级）')
  }

  const moveProbeFolder = async (folderId: string, targetParentId: string | null) => {
    if (targetParentId) {
      let walk: string | null = targetParentId
      const byId = new Map(probeState.folders.map(f => [f.id, f]))
      while (walk) {
        if (walk === folderId) {
          message.error('不能将目录移动到其子目录下')
          throw new Error('cycle')
        }
        walk = byId.get(walk)?.parentId ?? null
      }
    }
    setProbeState(prev => ({
      ...prev,
      folders: prev.folders.map(f =>
        f.id === folderId
          ? {
              ...f,
              parentId: targetParentId,
              sort_order: sortOrderForNewFolder(
                prev.folders.filter(x => x.id !== folderId),
                targetParentId,
              ),
            }
          : f,
      ),
    }))
  }

  const moveAndReorderProbeScripts = async (opts: {
    leafId: string
    targetFolderId: string | null
    orderedLeafIds: string[]
    folderChanged: boolean
  }) => {
    const { leafId, targetFolderId, orderedLeafIds } = opts
    setProbeState(prev => ({
      ...prev,
      scripts: prev.scripts.map(s => {
        const idx = orderedLeafIds.indexOf(s.id)
        if (idx < 0) return s
        return {
          ...s,
          ...(s.id === leafId ? { folderId: targetFolderId } : {}),
          sort_order: (idx + 1) * 10,
        }
      }),
    }))
    message.success('查询已移动')
  }

  const rightPane = (
    <>
      <StudioWorkbenchTopStrip padded>
        <StudioWorkbenchExpandSidebarButton
          collapsed={sidebarCollapsed}
          onExpand={() => setSidebarCollapsedPersist(false)}
          tooltip="显示探查目录"
        />
        <span style={{ fontWeight: 600, fontSize: 13 }}>数据探查</span>
        {activeScript && (
          <StudioWorkbenchActiveEntityTitle
            name={activeScript.name}
            testId="probe-active-script-title"
          />
        )}
      </StudioWorkbenchTopStrip>
      <StudioWorkbenchToolbar wrap>
        <Tooltip title="支持多条 SELECT（分号分隔）。已单独配置数据源的查询保持原配置；新建查询继承空间默认数据源。">
          <Select
            allowClear
            style={{ width: 280 }}
            value={hasExplicitDatasource(activeScript?.datasource_id) ? activeScript?.datasource_id : undefined}
            placeholder={
              probeDsResolve?.effectiveId
                ? (probeDsResolve.effective
                  ? `继承空间默认：${probeDsResolve.effective.name}`
                  : '继承空间默认')
                : '请先在空间设置配置默认数据源'
            }
            onChange={v => patchActiveScript({ datasource_id: v ?? undefined })}
            options={datasources.map((d: any) => ({ label: `${d.name} (${d.ds_type})`, value: d.id }))}
          />
        </Tooltip>
        {activeScript && probeDsResolve ? (
          <Tag
            style={{ margin: 0, maxWidth: 200 }}
            color={
              probeDsResolve.effectiveId
                ? (probeDsResolve.source === 'explicit' ? 'purple' : 'blue')
                : 'default'
            }
          >
            {datasourceTagText(probeDsResolve)}
          </Tag>
        ) : null}
        <Button icon={<FormatPainterOutlined />} onClick={formatSql} disabled={!sql.trim()}>
          格式化
        </Button>
        <AutosaveStatusHint
          visible={Boolean(activeScript)}
          status={scriptAutosave.status}
          hint={scriptAutosave.hint}
          localAuthority
        />
        <SqlRunWithRowLimitButton
          size="middle"
          limit={limit}
          loading={loading}
          disabled={!sql.trim()}
          onRun={() => { void run() }}
          onLimitChange={v => patchActiveScript({ limit: clampSqlResultRowLimit(v, PROBE_DEFAULT_ROW_LIMIT) })}
        />
        <Button
          icon={<AimOutlined />}
          onClick={locateActiveInTree}
          disabled={!activeScript}
          title="在探查目录中定位当前查询"
        >
          定位
        </Button>
        <div style={{ flex: 1 }} />
        <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
      </StudioWorkbenchToolbar>
      <StudioWorkbenchStage>
        {!treeReady ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 13 }}>
            <Spin />
            <span style={{ marginLeft: 10 }}>加载探查目录与脚本…</span>
          </div>
        ) : resultPanelOpen ? (
          <ResizableVerticalSplit
            storageKey="gido.probe.editorResultSplitRatio"
            defaultTopRatio={0.42}
            minTopRatio={0.2}
            minBottomRatio={0.22}
            top={(
              <div style={{ position: 'relative', height: '100%' }}>
                <MonacoFindBar getEditor={() => editorRef.current} apiRef={findApiRef} theme={editorAppearance.theme} />
                <Editor
                  key={activeScript?.id ?? 'probe'}
                  height="100%"
                  language="sql"
                  value={sql}
                  onChange={v => {
                    patchActiveScript({ sql: v || '' })
                    setSqlDirty(true)
                  }}
                  beforeMount={registerDwMonacoThemes}
                  onMount={(ed, monaco) => {
                    editorRef.current = ed
                    bindProbeScriptKeys(ed, monaco)
                  }}
                  theme={editorAppearance.theme}
                  options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), minimap: { enabled: false } }}
                />
              </div>
            )}
            bottom={(
              <InteractiveRunDock
                run={interactiveRun}
                scopeKey={`probe:${wsId}:${activeScript?.id ?? 'none'}`}
                onClose={() => setResultPanelOpen(false)}
              />
            )}
          />
        ) : (
          <div style={{ position: 'relative', height: '100%', minHeight: 0 }}>
            <MonacoFindBar getEditor={() => editorRef.current} apiRef={findApiRef} theme={editorAppearance.theme} />
            <Editor
              key={activeScript?.id ?? 'probe'}
              height="100%"
              language="sql"
              value={sql}
              onChange={v => {
                patchActiveScript({ sql: v || '' })
                setSqlDirty(true)
              }}
              beforeMount={registerDwMonacoThemes}
              onMount={(ed, monaco) => {
                editorRef.current = ed
                bindProbeScriptKeys(ed, monaco)
              }}
              theme={editorAppearance.theme}
              options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), minimap: { enabled: false } }}
            />
          </div>
        )}
      </StudioWorkbenchStage>
    </>
  )


  if (!wsId) {
    return <Alert type="warning" showIcon message="请先选择工作区" />
  }

  return (
    <>
      <StudioWorkbenchShell
        storageKey="gido.probe.sidebarWidth"
        defaultWidth={240}
        minWidth={180}
        maxWidth={520}
        collapsed={sidebarCollapsed}
        sidebarTitle="探查目录"
        sidebarActions={(
          <>
                <Tooltip title="新建目录">
                  <Button type="text" size="small" icon={<FolderAddOutlined />} onClick={() => addFolder(null)} />
                </Tooltip>
                <Tooltip title="新建查询">
                  <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => addScript(null)} />
                </Tooltip>
                <Tooltip title="隐藏探查目录">
                  <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setSidebarCollapsedPersist(true)} />
                </Tooltip>
          </>
        )}
        treeBodyClassName="probe-script-tree"
        tree={(
              <WorkspaceFolderTree
                rootTitle="探查查询"
                treeClassName="probe-script-tree"
                showRootCreateButton={false}
                folders={treeFolders}
                leaves={treeLeaves}
                expandedKeys={treeExpandedKeys}
                onExpandedKeysChange={setTreeExpandedKeys}
                selectedLeafId={activeScript?.id ?? null}
                onSelectLeaf={leaf => {
                  setProbeState(prev => ({ ...prev, activeScriptId: leaf.id }))
                  setResultPanelOpen(false)
                }}
                onCreateFolder={parentId => addFolder(parentId)}
                onRenameFolder={async (id, name) => {
                  setProbeState(prev => ({
                    ...prev,
                    folders: prev.folders.map(f => (f.id === id ? { ...f, name } : f)),
                  }))
                }}
                onDeleteFolder={deleteFolder}
                onRenameLeaf={async (id, name) => {
                  setProbeState(prev => ({
                    ...prev,
                    scripts: prev.scripts.map(s => (s.id === id ? { ...s, name } : s)),
                  }))
                }}
                onDeleteLeaf={leaf => {
                  Modal.confirm({
                    title: '删除探查查询？',
                    content: leaf.name,
                    onOk: () => deleteScript(leaf.id),
                  })
                }}
                onCopyLeaf={leaf => copyScript(leaf.id)}
                onMoveAndReorder={moveAndReorderProbeScripts}
                onMoveFolder={async ({ folderId, targetParentId }) => {
                  await moveProbeFolder(folderId, targetParentId)
                }}
                folderMenuExtra={f => [
                  { key: 'add-s', label: '新建查询', onClick: () => addScript(f.id) },
                ]}
              />
        )}
      >
        {rightPane}
      </StudioWorkbenchShell>

      <Modal title="新建目录" open={folderModal} onOk={handleCreateFolder} onCancel={() => { setFolderModal(false); setFolderParentId(null) }} width={360}>
        <Form form={folderForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="目录名称" rules={[{ required: true }]}>
            <Input placeholder="如：临时分析" />
          </Form.Item>
          {folderParentId && (
            <div style={{ color: '#999', fontSize: 12 }}>父目录：{probeState.folders.find(f => f.id === folderParentId)?.name}</div>
          )}
        </Form>
      </Modal>
    </>
  )
}

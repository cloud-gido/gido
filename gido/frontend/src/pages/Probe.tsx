/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useCallback, useMemo, useRef, type Key } from 'react'
import {
  Button, Select, InputNumber, Alert, Space, message, Input, Modal, Form, Tooltip, Tabs, Tag,
} from 'antd'
import {
  PlayCircleOutlined, DownloadOutlined, PlusOutlined, FolderAddOutlined,
  FormatPainterOutlined, MenuFoldOutlined, MenuUnfoldOutlined, AimOutlined,
} from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { format as sqlFormat } from 'sql-formatter'
import { probeApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableSidebar from '../components/ResizableSidebar'
import ResizableVerticalSplit from '../components/ResizableVerticalSplit'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import MonacoFindBar, { bindMonacoFindKeybindings, type MonacoFindBarApi } from '../components/MonacoFindBar'
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import QueryResultPanel from '../components/QueryResultPanel'
import EditorResultDock, { EditorResultRowBadge } from '../components/EditorResultDock'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { exportRowsToCsv } from '../utils/csvExport'
import { pruneWidths, resolveResultColumnOrder } from '../utils/resultTableMeta'
import {
  datasourceTagText,
  hasExplicitDatasource,
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
} from '../utils/probeLocalStore'
import WorkspaceFolderTree, { locateLeafInFolderTree, type FolderRow, type LeafRow } from '../components/WorkspaceFolderTree'
import AutosaveStatusHint from '../components/AutosaveStatusHint'
import { useScriptAutosave } from '../hooks/useScriptAutosave'

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
  const [datasources, setDatasources] = useState<any[]>([])
  const [probeState, setProbeState] = useState<ProbeWorkspaceState>(() => defaultProbeState())
  const [loading, setLoading] = useState(false)
  type StmtResult = {
    index: number
    sql: string
    columns: string[]
    column_types?: string[]
    rows: unknown[][]
    total: number
    truncated?: boolean
    error?: string | null
  }
  type ProbeRunResult = {
    statement_count: number
    statements: StmtResult[]
    columns: string[]
    column_types?: string[]
    rows: unknown[][]
    total: number
    truncated?: boolean
    has_errors?: boolean
  }
  const [result, setResult] = useState<ProbeRunResult | null>(null)
  const [activeResultTab, setActiveResultTab] = useState('0')
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

  useEffect(() => {
    if (!wsId) return
    datasourceApi.list(wsId).then((d: any) => {
      setDatasources(Array.isArray(d) ? d : [])
    })
    const loaded = loadProbeState(wsId)
    if (loaded) setProbeState(loaded)
    else {
      const init = defaultProbeState()
      setProbeState(init)
      saveProbeState(wsId, init)
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
    if (!wsId) return
    const t = window.setTimeout(() => saveProbeState(wsId, probeState), 280)
    return () => window.clearTimeout(t)
  }, [wsId, probeState])

  const activeScript = useMemo(
    () => probeState.scripts.find(s => s.id === probeState.activeScriptId) ?? null,
    [probeState.scripts, probeState.activeScriptId],
  )

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

  const activeStmt = useMemo(() => {
    if (!result?.statements?.length) return null
    const idx = Number(activeResultTab)
    return result.statements.find(s => s.index === idx) ?? result.statements[0]
  }, [result, activeResultTab])

  const displayColMeta = useMemo(() => {
    const cols = activeStmt?.columns
    if (!cols?.length) {
      return { order: [] as string[], widths: {} as Record<string, number>, sourceKeys: [] as string[] }
    }
    const m = activeScript?.resultColMeta ?? { order: [], widths: {} }
    return {
      order: resolveResultColumnOrder(m.order, cols, m.sourceKeys),
      widths: pruneWidths(m.widths, cols),
      sourceKeys: cols,
    }
  }, [activeStmt?.columns, activeScript?.resultColMeta])

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

  const onResultColumnOrderChange = useCallback(
    (nextOrder: string[]) => {
      patchActiveScript({
        resultColMeta: {
          order: nextOrder,
          widths: displayColMeta.widths,
          sourceKeys: displayColMeta.sourceKeys,
        },
      })
    },
    [patchActiveScript, displayColMeta.widths, displayColMeta.sourceKeys],
  )

  const onResultColumnWidthChange = useCallback(
    (key: string, width: number) => {
      patchActiveScript({
        resultColMeta: {
          order: displayColMeta.order,
          widths: { ...displayColMeta.widths, [key]: width },
          sourceKeys: displayColMeta.sourceKeys,
        },
      })
    },
    [patchActiveScript, displayColMeta.order, displayColMeta.widths, displayColMeta.sourceKeys],
  )

  const sql = activeScript?.sql ?? ''
  const limit = activeScript?.limit ?? 500

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

  useEffect(() => {
    if (!datasources.length || !activeScript) return
    if (!hasExplicitDatasource(activeScript.datasource_id)) return
    const valid = datasources.some((d: any) => d.id === activeScript.datasource_id)
    if (!valid) {
      message.warning('此查询绑定的数据源已删除，请重新在配置中选择或清空以继承空间默认')
      patchActiveScript({ datasource_id: undefined })
    }
  }, [datasources, activeScript?.id, activeScript?.datasource_id, patchActiveScript])

  const run = async () => {
    if (!wsId || !activeScript) {
      message.warning('请选择或新建一条探查查询')
      return
    }
    const runDs = probeDsResolve?.effectiveId
    if (!runDs) {
      message.warning('请先在「空间设置」配置默认数据源，或在本查询上单独选择数据源')
      return
    }
    setLoading(true)
    setResult(null)
    setResultPanelOpen(true)
    try {
      const res: any = await probeApi.query({
        workspace_id: wsId,
        datasource_id: runDs,
        sql: activeScript.sql,
        limit: activeScript.limit,
      })
      const runRes = res as ProbeRunResult
      setResult(runRes)
      const firstOk = runRes.statements?.find(s => !s.error && s.columns?.length) ?? runRes.statements?.[0]
      setActiveResultTab(String(firstOk?.index ?? 0))
      const colKeys = firstOk?.columns ?? runRes.columns
      if (colKeys?.length) {
        setProbeState(prev => {
          const id = prev.activeScriptId
          if (!id) return prev
          return {
            ...prev,
            scripts: prev.scripts.map(s => {
              if (s.id !== id) return s
              const m = s.resultColMeta ?? { order: [], widths: {} }
              // 新结果以 SQL/JDBC 列序为准；仅列签名不变时保留拖拽（见 resolveResultColumnOrder）
              const order = resolveResultColumnOrder(m.order, colKeys, m.sourceKeys)
              return {
                ...s,
                resultColMeta: {
                  order,
                  widths: pruneWidths(m.widths, colKeys),
                  sourceKeys: colKeys,
                },
              }
            }),
          }
        })
      }
      if (runRes.has_errors) message.warning('部分语句执行失败，请查看对应结果页签')
      else if (runRes.statement_count > 1) message.success(`已执行 ${runRes.statement_count} 条语句`)
      if (firstOk?.truncated) message.info(`结果已按最大 ${activeScript.limit} 行截断`)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '执行失败')
    }
    setLoading(false)
  }

  const formatSql = () => {
    const raw = (activeScript?.sql || '').trim()
    if (!raw) return
    try {
      const formatted = sqlFormat(raw, { language: 'postgresql', tabWidth: 2, keywordCase: 'upper' })
      patchActiveScript({ sql: formatted })
      message.success('已格式化 SQL')
    } catch {
      try {
        const formatted = sqlFormat(raw, { language: 'mysql', tabWidth: 2, keywordCase: 'upper' })
        patchActiveScript({ sql: formatted })
        message.success('已格式化 SQL')
      } catch (e: any) {
        message.error(e?.message || '格式化失败，请检查 SQL 语法')
      }
    }
  }

  const { dataSource, tableColumns } = useMemo(() => {
    if (!activeStmt?.columns?.length || activeStmt.error) {
      return { dataSource: [] as ReturnType<typeof rowsToRecordDataSource>, tableColumns: buildQueryTableColumns([]) }
    }
    const colMetas = normalizeQueryColumns(activeStmt.columns, activeStmt.column_types)
    const dataSource = rowsToRecordDataSource(activeStmt.columns, activeStmt.rows)
    return {
      dataSource,
      tableColumns: buildQueryTableColumns(colMetas, {
        order: displayColMeta.order,
        widths: displayColMeta.widths,
        dataSource,
        onOrderChange: onResultColumnOrderChange,
        onWidthChange: onResultColumnWidthChange,
      }),
    }
  }, [activeStmt, displayColMeta, onResultColumnOrderChange, onResultColumnWidthChange])

  const exportCsv = () => {
    if (!activeStmt?.columns?.length || !activeScript || activeStmt.error) return
    exportRowsToCsv(
      activeStmt.columns,
      activeStmt.rows as unknown[][],
      `probe_${activeScript.id}_${activeStmt.index}_${Date.now()}`,
    )
    message.success('已导出 CSV（UTF-8，Excel 可直接打开）')
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
          limit: 500,
          sort_order: sortOrderForNewScript(prev.scripts, folderId),
          // 新建查询不写入 datasource_id，运行期继承空间默认
        },
      ],
      activeScriptId: id,
    }))
    setResult(null)
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
    setResult(null)
    message.success('已删除')
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

  const reorderProbeFolders = async (parentId: string | null, orderedFolderIds: string[]) => {
    setProbeState(prev => ({
      ...prev,
      folders: prev.folders.map(f => {
        const idx = orderedFolderIds.indexOf(f.id)
        if (idx < 0) return f
        return { ...f, sort_order: (idx + 1) * 10 }
      }),
    }))
  }

  const moveAndReorderProbeScripts = async (opts: {
    leafId: string
    targetFolderId: string | null
    orderedLeafIds: string[]
    folderChanged: boolean
  }) => {
    const { leafId, targetFolderId, orderedLeafIds, folderChanged } = opts
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
    message.success(folderChanged ? '查询已移动' : '查询顺序已更新')
  }

  const rightPane = (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        {sidebarCollapsed && (
          <Tooltip title="显示探查目录">
            <Button type="text" size="small" icon={<MenuUnfoldOutlined />} onClick={() => setSidebarCollapsedPersist(false)} />
          </Tooltip>
        )}
        <h2 style={{ margin: 0 }}>数据探查</h2>
      </div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="支持多条 SELECT（分号分隔）。已单独配置数据源的查询保持原配置；新建查询继承空间默认数据源。"
      />
      <Space wrap style={{ marginBottom: 8 }}>
        <Select
          allowClear
          style={{ width: 280 }}
          value={hasExplicitDatasource(activeScript?.datasource_id) ? activeScript?.datasource_id : undefined}
          placeholder={
            probeDsResolve?.effective
              ? `继承空间默认：${probeDsResolve.effective.name}`
              : '请先在空间设置配置默认数据源'
          }
          onChange={v => patchActiveScript({ datasource_id: v ?? undefined })}
          options={datasources.map((d: any) => ({ label: `${d.name} (${d.ds_type})`, value: d.id }))}
        />
        {probeDsResolve && (
          <Tag color={probeDsResolve.effectiveId ? (probeDsResolve.source === 'explicit' ? 'purple' : 'blue') : 'default'}>
            {datasourceTagText(probeDsResolve)}
          </Tag>
        )}
        <span>最大行数</span>
        <InputNumber
          min={1}
          max={10000}
          value={limit}
          onChange={v => patchActiveScript({ limit: Number(v) || 500 })}
        />
        <Button icon={<FormatPainterOutlined />} onClick={formatSql} disabled={!sql.trim()}>
          格式化
        </Button>
        <AutosaveStatusHint
          visible={Boolean(activeScript)}
          status={scriptAutosave.status}
          hint={scriptAutosave.hint}
          localAuthority
        />
        <Button type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={run}>
          运行
        </Button>
        <Button
          icon={<AimOutlined />}
          onClick={locateActiveInTree}
          disabled={!activeScript}
          title="在探查目录中定位当前查询"
        >
          定位
        </Button>
        {activeStmt && !activeStmt.error && (
          <Button icon={<DownloadOutlined />} onClick={exportCsv}>
            导出 CSV（最多 {activeStmt.rows.length} 行）
          </Button>
        )}
        <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
      </Space>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
        {resultPanelOpen ? (
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
                    bindMonacoFindKeybindings(ed, monaco, () => findApiRef.current)
                  }}
                  theme={editorAppearance.theme}
                  options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), minimap: { enabled: false } }}
                />
              </div>
            )}
            bottom={(
              <EditorResultDock
                activeKey="result"
                onClose={() => setResultPanelOpen(false)}
                tabs={[{
                  key: 'result',
                  label: (
                    <>
                      查询结果
                      {activeStmt && !activeStmt.error && (
                        <EditorResultRowBadge count={activeStmt.total} />
                      )}
                      {loading && <span style={{ marginLeft: 8, color: '#999', fontSize: 12, fontWeight: 400 }}>执行中…</span>}
                    </>
                  ),
                  children: (
                    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', height: '100%' }}>
                      {result ? (
                        <>
                          {result.statement_count > 1 && (
                            <Tabs
                              size="small"
                              activeKey={activeResultTab}
                              onChange={setActiveResultTab}
                              style={{ padding: '0 8px', flexShrink: 0 }}
                              items={result.statements.map(s => ({
                                key: String(s.index),
                                label: s.error ? `语句 ${s.index + 1} ✕` : `语句 ${s.index + 1}`,
                              }))}
                            />
                          )}
                          {activeStmt?.error ? (
                            <Alert type="error" showIcon message="执行失败" description={activeStmt.error} style={{ margin: 12 }} />
                          ) : (
                            <QueryResultPanel
                              dataSource={dataSource}
                              columns={tableColumns}
                              toolbar={(
                                <div style={{ padding: '8px 12px', fontSize: 12, color: '#666' }}>
                                  共 <strong>{activeStmt?.total ?? 0}</strong> 行
                                  {activeStmt?.truncated ? `（已按上限 ${limit} 截断）` : ''}
                                  ；表头右上角为类型徽章；支持多条语句（分号分隔）
                                </div>
                              )}
                            />
                          )}
                        </>
                      ) : (
                        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 13 }}>
                          {loading ? '执行中…' : '运行后在此展示查询结果（可用分号分隔多条 SELECT）'}
                        </div>
                      )}
                    </div>
                  ),
                }]}
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
                bindMonacoFindKeybindings(ed, monaco, () => findApiRef.current)
              }}
              theme={editorAppearance.theme}
              options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), minimap: { enabled: false } }}
            />
          </div>
        )}
      </div>
    </div>
  )

  if (!wsId) {
    return <Alert type="warning" showIcon message="请先选择工作区" />
  }

  return (
    <>
      <ResizableSidebar
        storageKey="gido.probe.sidebarWidth"
        defaultWidth={240}
        minWidth={180}
        maxWidth={520}
        collapsed={sidebarCollapsed}
        style={{ height: 'calc(100vh - 112px)', margin: -24, overflow: 'hidden' }}
        left={(
          <div style={{ display: 'flex', flexDirection: 'column', background: '#fafafa', height: '100%', minHeight: 0 }}>
            <div style={{ padding: '10px 12px', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>探查目录</span>
              <Space size={0}>
                <Tooltip title="新建目录">
                  <Button type="text" size="small" icon={<FolderAddOutlined />} onClick={() => addFolder(null)} />
                </Tooltip>
                <Tooltip title="新建查询">
                  <Button type="text" size="small" icon={<PlusOutlined />} onClick={() => addScript(null)} />
                </Tooltip>
                <Tooltip title="隐藏探查目录">
                  <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={() => setSidebarCollapsedPersist(true)} />
                </Tooltip>
              </Space>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }} className="probe-script-tree">
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
                  setResult(null)
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
                onMoveAndReorder={moveAndReorderProbeScripts}
                onMoveFolder={async ({ folderId, targetParentId }) => {
                  await moveProbeFolder(folderId, targetParentId)
                }}
                onReorderFolders={async ({ parentId, orderedFolderIds }) => {
                  await reorderProbeFolders(parentId, orderedFolderIds)
                }}
                folderMenuExtra={f => [
                  { key: 'add-s', label: '新建查询', onClick: () => addScript(f.id) },
                ]}
              />
            </div>
          </div>
        )}
        right={rightPane}
      />

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

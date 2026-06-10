/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Button, Select, InputNumber, Alert, Space, message, Tree, Input, Modal, Form, Dropdown, Tooltip, Tabs, Tag,
} from 'antd'
import {
  PlayCircleOutlined, DownloadOutlined, PlusOutlined, FolderAddOutlined, FolderOutlined, FileOutlined,
  MoreOutlined, FormatPainterOutlined,
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
import { buildQueryTableColumns, rowsToRecordDataSource } from '../components/QueryResultTable'
import QueryResultPanel from '../components/QueryResultPanel'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { exportRowsToCsv } from '../utils/csvExport'
import { mergeColumnOrderWithKeys, pruneWidths } from '../utils/resultTableMeta'
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
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())

  const [folderModal, setFolderModal] = useState(false)
  const [folderForm] = Form.useForm()
  const [folderParentId, setFolderParentId] = useState<string | null>(null)
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null)
  const [renamingFolderName, setRenamingFolderName] = useState('')
  const [renamingScriptId, setRenamingScriptId] = useState<string | null>(null)
  const [renamingScriptName, setRenamingScriptName] = useState('')

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

  useEffect(() => {
    if (!wsId) return
    const t = window.setTimeout(() => saveProbeState(wsId, probeState), 280)
    return () => window.clearTimeout(t)
  }, [wsId, probeState])

  const activeScript = useMemo(
    () => probeState.scripts.find(s => s.id === probeState.activeScriptId) ?? null,
    [probeState.scripts, probeState.activeScriptId],
  )

  const activeStmt = useMemo(() => {
    if (!result?.statements?.length) return null
    const idx = Number(activeResultTab)
    return result.statements.find(s => s.index === idx) ?? result.statements[0]
  }, [result, activeResultTab])

  const displayColMeta = useMemo(() => {
    const cols = activeStmt?.columns
    if (!cols?.length) return { order: [] as string[], widths: {} as Record<string, number> }
    const m = activeScript?.resultColMeta ?? { order: [], widths: {} }
    return {
      order: mergeColumnOrderWithKeys(m.order, cols),
      widths: pruneWidths(m.widths, cols),
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
        resultColMeta: { order: nextOrder, widths: displayColMeta.widths },
      })
    },
    [patchActiveScript, displayColMeta.widths],
  )

  const onResultColumnWidthChange = useCallback(
    (key: string, width: number) => {
      patchActiveScript({
        resultColMeta: { order: displayColMeta.order, widths: { ...displayColMeta.widths, [key]: width } },
      })
    },
    [patchActiveScript, displayColMeta.order, displayColMeta.widths],
  )

  const sql = activeScript?.sql ?? ''
  const limit = activeScript?.limit ?? 500

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
              return {
                ...s,
                resultColMeta: {
                  order: mergeColumnOrderWithKeys(m.order, colKeys),
                  widths: pruneWidths(m.widths, colKeys),
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
      folders: [...prev.folders, { id, name: v.name, parentId: folderParentId }],
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

  const deleteFolder = (folderId: string) => {
    const hasChildFolders = probeState.folders.some(f => f.parentId === folderId)
    const hasScripts = probeState.scripts.some(s => s.folderId === folderId)
    if (hasChildFolders || hasScripts) {
      message.warning('请先清空子目录与目录内查询后再删除')
      return
    }
    setProbeState(prev => ({
      ...prev,
      folders: prev.folders.filter(f => f.id !== folderId),
    }))
    message.success('已删除目录')
  }

  const handleRenameFolder = (folderId: string) => {
    const name = renamingFolderName.trim()
    if (!name) return
    setProbeState(prev => ({
      ...prev,
      folders: prev.folders.map(f => (f.id === folderId ? { ...f, name } : f)),
    }))
    setRenamingFolderId(null)
  }

  const handleRenameScript = (scriptId: string) => {
    const name = renamingScriptName.trim()
    if (!name) return
    setProbeState(prev => ({
      ...prev,
      scripts: prev.scripts.map(s => (s.id === scriptId ? { ...s, name } : s)),
    }))
    setRenamingScriptId(null)
  }

  const buildTreeData = () => {
    const folderMap: Record<string, any> = {}
    probeState.folders.forEach(f => {
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
              { key: 'add-s', label: '新建查询', onClick: () => addScript(f.id) },
              { key: 'add-f', label: '新建子目录', onClick: () => addFolder(f.id) },
              { key: 'rn', label: '重命名', onClick: () => { setRenamingFolderId(f.id); setRenamingFolderName(f.name) } },
              { key: 'del', label: <span style={{ color: 'red' }}>删除目录</span>, onClick: () => deleteFolder(f.id) },
            ] }} trigger={['click']}>
              <MoreOutlined style={{ padding: '0 4px', color: '#999' }} onClick={e => e.stopPropagation()} />
            </Dropdown>
          </div>
        ),
        children: [] as any[],
        isLeaf: false,
        _folderId: f.id,
        _parentId: f.parentId,
      }
    })
    const rootScripts: any[] = []
    probeState.scripts.forEach(s => {
      const nodeItem = {
        key: `script-${s.id}`,
        title: renamingScriptId === s.id ? (
          <Input
            size="small"
            autoFocus
            defaultValue={s.name}
            style={{ width: 130 }}
            onChange={e => setRenamingScriptName(e.target.value)}
            onPressEnter={() => handleRenameScript(s.id)}
            onBlur={() => handleRenameScript(s.id)}
            onClick={e => e.stopPropagation()}
          />
        ) : (
          <div
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}
            onDoubleClick={() => { setRenamingScriptId(s.id); setRenamingScriptName(s.name) }}
          >
            <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <FileOutlined style={{ marginRight: 6, color: '#1677ff' }} />{s.name}
            </span>
            <Dropdown menu={{ items: [
              { key: 'rn', label: '重命名', onClick: () => { setRenamingScriptId(s.id); setRenamingScriptName(s.name) } },
              { key: 'del', label: <span style={{ color: 'red' }}>删除</span>, onClick: () => deleteScript(s.id) },
            ] }} trigger={['click']}>
              <MoreOutlined style={{ padding: '0 4px', color: '#999' }} onClick={e => e.stopPropagation()} />
            </Dropdown>
          </div>
        ),
        isLeaf: true,
        _scriptId: s.id,
      }
      if (s.folderId && folderMap[s.folderId]) {
        folderMap[s.folderId].children.push(nodeItem)
      } else {
        rootScripts.push(nodeItem)
      }
    })
    const rootFolders: any[] = []
    Object.values(folderMap).forEach((f: any) => {
      if (f._parentId && folderMap[f._parentId]) {
        folderMap[f._parentId].children.unshift(f)
      } else {
        rootFolders.push(f)
      }
    })
    return [
      {
        key: 'root',
        title: <span style={{ fontWeight: 600 }}><FolderOutlined style={{ marginRight: 6 }} />探查查询</span>,
        children: [...rootFolders, ...rootScripts],
      },
    ]
  }

  const rightPane = (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <h2 style={{ margin: '0 0 8px' }}>数据探查</h2>
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
        <Button type="primary" icon={<PlayCircleOutlined />} loading={loading} onClick={run}>
          运行
        </Button>
        {activeStmt && !activeStmt.error && (
          <Button icon={<DownloadOutlined />} onClick={exportCsv}>
            导出 CSV（最多 {activeStmt.rows.length} 行）
          </Button>
        )}
        <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
      </Space>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
        <ResizableVerticalSplit
          storageKey="gido.probe.editorResultSplitRatio"
          defaultTopRatio={0.42}
          minTopRatio={0.2}
          minBottomRatio={0.22}
          top={(
            <Editor
              key={activeScript?.id ?? 'probe'}
              height="100%"
              language="sql"
              value={sql}
              onChange={v => patchActiveScript({ sql: v || '' })}
              beforeMount={registerDwMonacoThemes}
              theme={editorAppearance.theme}
              options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), minimap: { enabled: false } }}
            />
          )}
          bottom={(
            <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, background: '#fff' }}>
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
                  运行后在此展示查询结果（可用分号分隔多条 SELECT）
                </div>
              )}
            </div>
          )}
        />
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
              </Space>
            </div>
            <div style={{ flex: 1, overflow: 'auto', padding: '4px 0' }}>
              <Tree
                treeData={buildTreeData()}
                defaultExpandAll
                blockNode
                selectedKeys={activeScript ? [`script-${activeScript.id}`] : []}
                onSelect={(keys, { node }: any) => {
                  const sid = node?._scriptId as string | undefined
                  if (sid) {
                    setProbeState(prev => ({ ...prev, activeScriptId: sid }))
                    setResult(null)
                  }
                }}
                style={{ background: 'transparent' }}
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

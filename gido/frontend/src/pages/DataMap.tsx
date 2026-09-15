/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Table, Input, Button, Modal, Form, Select, Tag, Space, message, Descriptions, Tabs, Alert, Spin } from 'antd'
import { SearchOutlined, PlusOutlined, SyncOutlined, ApartmentOutlined, UploadOutlined } from '@ant-design/icons'
import { datamapApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { formatCellDisplay } from '../utils/cellDisplay'
import LineageGraph from '../components/LineageGraph'
import SoftRowDetailToggle from '../components/SoftRowDetailToggle'
import { useSoftExpandedRows } from '../hooks/useSoftExpandedRows'
import { useResizableTableColumns } from '../hooks/useResizableTableColumns'
import { R } from '../routes'
import { peekCachedDatasources, rememberDatasources } from '../utils/workspaceDatasource'
import {
  catalogCapableDatasources,
  mergeCatalogWithRegistered,
  replaceDatasourceCatalogRows,
} from '../utils/dataMapCatalogLoad'

/** 同会话内默认目录视图（无关键字）内存缓存，菜单来回切换先出表再静默刷新 */
const catalogViewCache = new Map<string, any[]>()

function catalogCacheKey(wsId: number, dsFilter?: number) {
  return `${wsId}|${dsFilter ?? ''}`
}

function datamapErrMsg(e: any, fallback: string) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const status = e?.response?.status
  if (status === 403) return '无权限：数据分析等只读角色可浏览目录，收录/同步需具备数据字典写权限'
  if (status === 401) return '登录已失效，请重新登录后再试'
  return fallback
}

function tableTypeLabel(t: string | undefined) {
  if (!t) return '—'
  return String(t).toLowerCase().includes('view') ? 'VIEW' : 'TABLE'
}

export default function DataMapPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canWrite = can(user, P.GIDO_BATCH_DATAMAP_WRITE, currentWorkspace)
  const navigate = useNavigate()
  const { isExpanded, toggle, expandableControl } = useSoftExpandedRows()
  const [tables, setTables] = useState<any[]>(() => {
    const id = useAppStore.getState().currentWorkspace?.id
    if (id == null) return []
    return catalogViewCache.get(catalogCacheKey(id)) ?? []
  })
  const [datasources, setDatasources] = useState<any[]>(() =>
    peekCachedDatasources(useAppStore.getState().currentWorkspace?.id),
  )
  const [keyword, setKeyword] = useState('')
  const [dsFilter, setDsFilter] = useState<number | undefined>(undefined)
  const [detailModal, setDetailModal] = useState(false)
  const [selectedTable, setSelectedTable] = useState<any>(null)
  const [lineageData, setLineageData] = useState<{ nodes: any[], edges: any[] }>({ nodes: [], edges: [] })
  const [impactData, setImpactData] = useState<any[]>([])
  const [lineageLoading, setLineageLoading] = useState(false)
  const [impactLoading, setImpactLoading] = useState(false)
  const [detailExtrasLoaded, setDetailExtrasLoaded] = useState<{ lineage?: boolean; impact?: boolean }>({})
  const [previewData, setPreviewData] = useState<any>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [registerModal, setRegisterModal] = useState(false)
  const [form] = Form.useForm()
  const [listLoading, setListLoading] = useState(false)
  /** 仅用户点「刷新目录」时转圈，后台静默同步不碰工具条（避免闪） */
  const [refreshing, setRefreshing] = useState(false)
  /** 行展开明细：字段字典等 */
  const [rowPanel, setRowPanel] = useState<Record<string, { loading: boolean; detail?: any; error?: string }>>({})
  /** 旧 DESCRIBE 同步无注释：每个 meta 表本会话最多自动补同步一次 */
  const commentHealTriedRef = useRef<Set<number>>(new Set())
  const loadGenRef = useRef(0)
  const keywordRef = useRef(keyword)
  keywordRef.current = keyword
  const dsFilterRef = useRef(dsFilter)
  dsFilterRef.current = dsFilter

  const patchCatalogRow = useCallback((rowKey: string, patch: Record<string, unknown>) => {
    setTables((prev) => {
      const next = prev.map((r) => (r.rowKey === rowKey ? { ...r, ...patch } : r))
      if (wsId != null && !(keywordRef.current || '').trim()) {
        catalogViewCache.set(catalogCacheKey(wsId, dsFilterRef.current), next)
      }
      return next
    })
  }, [wsId])

  const loadDatasources = useCallback(async (): Promise<any[]> => {
    if (!wsId) return []
    try {
      const d: any = await datasourceApi.list(wsId).catch(() => [])
      const list = Array.isArray(d) ? d : []
      rememberDatasources(wsId, list)
      setDatasources(list)
      return list
    } catch {
      return peekCachedDatasources(wsId)
    }
  }, [wsId])

  /**
   * 分源渐进加载（对齐探查：有缓存则先稳住画面，后台静默对齐，不中途清空/缩表）。
   * 无缓存：先出已注册字典，再按源补物理表。工具条不展示同步进度，避免闪来闪去。
   */
  const loadCatalog = useCallback(async (opts?: { userRefresh?: boolean }) => {
    if (!wsId) return
    const gen = ++loadGenRef.current
    const userRefresh = Boolean(opts?.userRefresh)
    if (userRefresh) setRefreshing(true)
    const kw = (keywordRef.current || '').trim()
    const filter = dsFilterRef.current
    const cacheKey = catalogCacheKey(wsId, filter)
    const cached = !kw ? catalogViewCache.get(cacheKey) : undefined
    const hadCache = Boolean(cached?.length)
    if (hadCache) {
      setTables(cached!)
      setListLoading(false)
    } else if (!userRefresh) {
      setListLoading(true)
    }

    try {
      const regRaw: any = await datamapApi.searchTables(wsId, kw || undefined).catch(() => [])
      if (gen !== loadGenRef.current) return
      const registered = Array.isArray(regRaw) ? regRaw : []
      let catalogRows: any[] = []
      // 有缓存时禁止中途改成「仅注册表」——那是一闪而过的割裂感来源
      if (!hadCache) {
        setTables(mergeCatalogWithRegistered(catalogRows, registered, filter))
        setListLoading(false)
      }

      let dsList = peekCachedDatasources(wsId)
      if (!dsList.length) {
        dsList = await loadDatasources()
        if (gen !== loadGenRef.current) return
      } else {
        // 有缓存：后台刷新数据源选项，不挡工具条、不 loading Select
        void loadDatasources()
      }
      const targets = catalogCapableDatasources(dsList as any[], filter)
      if (!targets.length) {
        const merged = mergeCatalogWithRegistered([], registered, filter)
        setTables(merged)
        if (!kw) catalogViewCache.set(cacheKey, merged)
        return
      }

      for (let i = 0; i < targets.length; i++) {
        const ds = targets[i]
        if (gen !== loadGenRef.current) return
        const c: any = await datamapApi
          .catalog(wsId, { datasource_id: ds.id, keyword: kw || undefined })
          .catch(() => [])
        if (gen !== loadGenRef.current) return
        const slice = Array.isArray(c) ? c : []
        catalogRows = replaceDatasourceCatalogRows(catalogRows, ds.id, slice)
        // 冷启动才边拉边刷；有缓存则等全部完成再一次替换（SWR）
        if (!hadCache) {
          setTables(mergeCatalogWithRegistered(catalogRows, registered, filter))
        }
      }

      const merged = mergeCatalogWithRegistered(catalogRows, registered, filter)
      setTables(merged)
      if (!kw) catalogViewCache.set(cacheKey, merged)
    } finally {
      if (gen === loadGenRef.current) {
        setListLoading(false)
        if (userRefresh) setRefreshing(false)
      }
    }
  }, [wsId, loadDatasources])

  const load = async () => {
    await Promise.all([loadDatasources(), loadCatalog({ userRefresh: true })])
  }

  useEffect(() => { void loadDatasources() }, [loadDatasources])
  useEffect(() => { void loadCatalog() }, [wsId, dsFilter, loadCatalog])

  const openDetail = async (table: { id: number }) => {
    try {
      const detail: any = await datamapApi.getTable(table.id)
      setSelectedTable(detail)
      setLineageData({ nodes: [], edges: [] })
      setImpactData([])
      setDetailExtrasLoaded({})
      setPreviewData(null)
      setDetailModal(true)
      // 注释自愈不挡首屏：先展示，后台补同步后再刷新抽屉
      scheduleCommentHeal(detail, (next) => setSelectedTable(next))
    } catch (e: any) {
      message.error(datamapErrMsg(e, '加载字典失败'))
    }
  }

  /**
   * 旧 DESCRIBE 同步无注释：先展示，后台 syncSchema 再刷新（不 await 挡屏）。
   * 真无 COMMENT 的表本会话只打源库一次。
   */
  const scheduleCommentHeal = (detail: any, onHealed: (next: any) => void) => {
    if (!canWrite || !detail?.id || !Array.isArray(detail.columns) || !detail.columns.length) return
    const allEmpty = detail.columns.every((c: any) => !String(c.comment || '').trim())
    if (!allEmpty) return
    const id = Number(detail.id)
    if (commentHealTriedRef.current.has(id)) return
    commentHealTriedRef.current.add(id)
    void (async () => {
      try {
        await datamapApi.syncSchema(id)
        const next = await datamapApi.getTable(id)
        onHealed(next)
      } catch {
        /* 保持当前展示 */
      }
    })()
  }

  /** 仅未收录时 ensure；已有 meta_table_id 稳态只读，不打 ensure */
  const ensureRowMeta = async (row: any): Promise<number | null> => {
    if (row?.meta_table_id) return Number(row.meta_table_id)
    if (row?.error || !row?.table_name || row?.datasource_id == null) return null
    if (!canWrite || !wsId) return null
    const ensured: any = await datamapApi.ensureTable({
      workspace_id: wsId,
      datasource_id: row.datasource_id,
      db_name: row.catalog || undefined,
      table_name: row.table_name,
      table_comment: row.table_comment || undefined,
      table_type: String(row.table_type || 'table').toLowerCase().includes('view') ? 'view' : 'table',
      sync_if_empty: true,
    })
    const id = Number(ensured?.id)
    if (!id) return null
    patchCatalogRow(row.rowKey, {
      registered: true,
      meta_table_id: id,
      table_comment: ensured.table_comment ?? row.table_comment,
      row_count: ensured.row_count ?? row.row_count,
      table_type: ensured.table_type ?? row.table_type,
    })
    return id
  }

  const loadExpandedPanel = async (row: any) => {
    const key = String(row.rowKey)
    setRowPanel((prev) => ({ ...prev, [key]: { loading: true, detail: prev[key]?.detail } }))
    try {
      let metaId = row.meta_table_id ? Number(row.meta_table_id) : null
      if (!metaId && canWrite) {
        metaId = await ensureRowMeta(row)
      }
      if (!metaId) {
        setRowPanel((prev) => ({
          ...prev,
          [key]: {
            loading: false,
            detail: null,
            error: canWrite ? undefined : '只读角色：展开仅展示目录摘要；收录需写权限',
          },
        }))
        return
      }
      const detail: any = await datamapApi.getTable(metaId)
      setRowPanel((prev) => ({ ...prev, [key]: { loading: false, detail } }))
      scheduleCommentHeal(detail, (next) => {
        setRowPanel((prev) => ({ ...prev, [key]: { loading: false, detail: next } }))
        patchCatalogRow(row.rowKey, {
          table_comment: next.table_comment ?? row.table_comment,
          row_count: next.row_count ?? row.row_count,
        })
      })
    } catch (e: any) {
      setRowPanel((prev) => ({
        ...prev,
        [key]: { loading: false, error: datamapErrMsg(e, '加载字段失败') },
      }))
    }
  }

  const handleToggleRow = (row: any) => {
    const key = row.rowKey
    const willExpand = !isExpanded(key)
    toggle(key)
    if (willExpand) void loadExpandedPanel(row)
  }

  const ensureDetailExtra = async (key: 'lineage' | 'impact') => {
    if (!selectedTable?.id || detailExtrasLoaded[key]) return
    if (key === 'lineage') {
      setLineageLoading(true)
      try {
        const lineage: any = await datamapApi.getLineage(selectedTable.id, 3)
        setLineageData(lineage || { nodes: [], edges: [] })
        setDetailExtrasLoaded(prev => ({ ...prev, lineage: true }))
      } catch (e: any) {
        message.error(e?.response?.data?.detail || '加载血缘失败')
      } finally {
        setLineageLoading(false)
      }
      return
    }
    setImpactLoading(true)
    try {
      const impact: any = await datamapApi.getImpact(selectedTable.id)
      setImpactData(impact?.impacted_tables || [])
      setDetailExtrasLoaded(prev => ({ ...prev, impact: true }))
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载影响分析失败')
    } finally {
      setImpactLoading(false)
    }
  }

  const loadPreview = async (tableId: number) => {
    setPreviewLoading(true)
    try {
      const res: any = await datamapApi.previewData(tableId, 100)
      setPreviewData(res)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '预览失败')
    }
    setPreviewLoading(false)
  }

  const handleSyncSchema = async (tableId: number) => {
    if (!canWrite) {
      message.warning('当前为只读角色，无法同步结构')
      return
    }
    try {
      const res: any = await datamapApi.syncSchema(tableId)
      message.success(`同步成功，共 ${res.columns} 个字段`)
      if (selectedTable?.id === tableId) openDetail({ id: tableId })
      setRowPanel((prev) => {
        const next = { ...prev }
        for (const [k, v] of Object.entries(next)) {
          if (v.detail?.id === tableId) {
            next[k] = { loading: true }
          }
        }
        return next
      })
      const row = tables.find((r) => Number(r.meta_table_id) === tableId)
      if (row) void loadExpandedPanel(row)
    } catch (e: any) {
      message.error(datamapErrMsg(e, '同步失败'))
    }
  }

  const handleRegister = async () => {
    if (!canWrite) {
      message.warning('当前为只读角色，无法收录表')
      return
    }
    try {
      const values = await form.validateFields()
      values.workspace_id = wsId
      const created: any = await datamapApi.ensureTable(values)
      if (created?.sync_warning) {
        message.warning(`已收录，但字段同步失败：${created.sync_warning}`)
      } else if (created?.created === false) {
        message.success('该表已在数据字典中')
      } else {
        message.success(`已收录，同步 ${created?.columns_synced ?? created?.columns?.length ?? 0} 个字段`)
      }
      setRegisterModal(false)
      await load()
      if (created?.id) openDetail({ id: created.id })
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(datamapErrMsg(e, '收录失败'))
    }
  }

  const openCatalogRow = async (row: any) => {
    if (row.error) {
      message.warning(`数据源 ${row.datasource_name} 拉表失败：${row.error}`)
      return
    }
    if (!canWrite && !(row.registered && row.meta_table_id)) {
      message.warning('当前为只读角色，可浏览物理表目录；打开收录需具备「数据字典写」权限')
      return
    }
    try {
      if (canWrite) {
        const id = await ensureRowMeta(row)
        if (id) openDetail({ id })
        return
      }
      if (row.meta_table_id) openDetail({ id: row.meta_table_id })
    } catch (e: any) {
      message.error(datamapErrMsg(e, '收录失败'))
    }
  }

  const columnsBase = [
    {
      title: '表',
      key: 'table',
      render: (_: unknown, row: any) => {
        if (row.error) {
          return (
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 500, color: '#cf1322' }}>{row.datasource_name || '数据源'}</div>
              <div style={{ color: '#8c8c8c', fontSize: 12 }}>{row.error}</div>
            </div>
          )
        }
        const sub = [row.datasource_name, row.catalog].filter(Boolean).join(' · ')
        return (
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {canWrite || row.registered
                ? <a onClick={() => openCatalogRow(row)}>{row.table_name || '—'}</a>
                : <span>{row.table_name || '—'}</span>}
            </div>
            <div style={{ color: '#8c8c8c', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={sub}>
                {sub || '—'}
              </span>
              {!row.error && (
                <SoftRowDetailToggle
                  expanded={isExpanded(row.rowKey)}
                  onToggle={() => handleToggleRow(row)}
                />
              )}
            </div>
          </div>
        )
      },
    },
    {
      title: '状态',
      key: 'status',
      width: 88,
      render: (_: unknown, row: any) => {
        if (row.error) return <Tag color="error">失败</Tag>
        return row.registered
          ? <Tag color="green">已收录</Tag>
          : <Tag>目录</Tag>
      },
    },
    {
      title: '描述',
      dataIndex: 'table_comment',
      key: 'comment',
      ellipsis: true,
      render: (c: string) => c || <span style={{ color: '#bfbfbf' }}>—</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      fixed: 'right' as const,
      render: (_: unknown, row: any) => (
        <Space size={4} wrap={false} style={{ whiteSpace: 'nowrap' }}>
          {!row.error && (
            <Button type="link" size="small" onClick={() => openCatalogRow(row)}>打开</Button>
          )}
          {row.meta_table_id && canWrite && (
            <Button
              type="link"
              size="small"
              icon={<SyncOutlined />}
              onClick={(e) => { e.stopPropagation(); handleSyncSchema(row.meta_table_id) }}
            >
              同步结构
            </Button>
          )}
          {row.meta_table_id && (
            <Button
              type="link"
              size="small"
              icon={<ApartmentOutlined />}
              onClick={(e) => { e.stopPropagation(); openDetail({ id: row.meta_table_id }) }}
            >
              字典
            </Button>
          )}
        </Space>
      ),
    },
  ]

  const columns = useResizableTableColumns(columnsBase, {
    storageKey: wsId ? `gido.datamap.catalog.cols.w${wsId}` : undefined,
    defaultWidths: {
      table: 320,
      status: 88,
      comment: 220,
      actions: 200,
    },
  })

  const renderRowDetail = (row: any) => {
    const panel = rowPanel[String(row.rowKey)]
    return (
      <div className="gido-soft-expanded-panel">
        <Descriptions size="small" column={2} style={{ marginBottom: 12 }}>
          <Descriptions.Item label="限定名" span={2}>
            {row.qualified_name || `${row.catalog || ''}.${row.table_name}`.replace(/^\./, '')}
          </Descriptions.Item>
          <Descriptions.Item label="类型">{tableTypeLabel(row.table_type)}</Descriptions.Item>
          <Descriptions.Item label="行数">{row.row_count ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="描述" span={2}>{row.table_comment || '—'}</Descriptions.Item>
        </Descriptions>
        {panel?.loading && (
          <div style={{ padding: 24, textAlign: 'center' }}><Spin tip="加载字段…" /></div>
        )}
        {!panel?.loading && panel?.error && (
          <Alert type="info" showIcon message={panel.error} style={{ marginBottom: 8 }} />
        )}
        {!panel?.loading && panel?.detail?.columns && (
          <>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>
              字段（{panel.detail.columns.length}）
              {canWrite && panel.detail.id && (
                <Button
                  type="link"
                  size="small"
                  icon={<SyncOutlined />}
                  onClick={() => handleSyncSchema(panel.detail.id)}
                >
                  同步结构
                </Button>
              )}
            </div>
            <Table
              size="small"
              pagination={false}
              rowKey={(c: any) => c.id ?? c.name}
              dataSource={panel.detail.columns}
              scroll={{ y: 240 }}
              columns={[
                { title: '字段', dataIndex: 'name', width: 140, ellipsis: true },
                { title: '类型', dataIndex: 'type', width: 120, ellipsis: true },
                { title: '描述', dataIndex: 'comment', ellipsis: true },
                {
                  title: '键',
                  dataIndex: 'primary_key',
                  width: 56,
                  render: (v: boolean) => (v ? <Tag color="gold">PK</Tag> : ''),
                },
              ]}
            />
            <div style={{ marginTop: 8 }}>
              <Button type="link" size="small" onClick={() => openDetail({ id: panel.detail.id })}>
                打开字典（血缘 / 样例）
              </Button>
            </div>
          </>
        )}
        {!panel?.loading && !panel?.detail && !panel?.error && !canWrite && (
          <Alert type="info" showIcon message="只读角色：可浏览目录摘要；字段字典需先由有写权限的同事打开收录。" />
        )}
      </div>
    )
  }

  const colColumns = [
    { title: '字段名', dataIndex: 'name' },
    { title: '类型', dataIndex: 'type' },
    { title: '描述', dataIndex: 'comment' },
    { title: '可空', dataIndex: 'nullable', render: (v: boolean) => v ? '是' : '否' },
    { title: '主键', dataIndex: 'primary_key', render: (v: boolean) => v ? <Tag color="gold">PK</Tag> : '' },
  ]

  const impactColumns = [
    { title: '数据库', dataIndex: 'db_name' },
    { title: '表名', dataIndex: 'table_name' },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, minHeight: 32 }}>
        <h2 style={{ margin: 0 }}>数据地图</h2>
        <Space wrap size={8} style={{ minHeight: 32, alignItems: 'center' }}>
          <Select
            allowClear
            placeholder="筛选数据源"
            style={{ width: 200 }}
            value={dsFilter}
            onChange={v => setDsFilter(v)}
            options={datasources.map((d: any) => ({ label: d.name, value: d.id }))}
          />
          <Input.Search
            placeholder="搜索表名 / 库名 / 描述"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={() => { void loadCatalog({ userRefresh: true }) }}
            style={{ width: 260 }}
          />
          {canWrite && (
            <>
              <Button icon={<PlusOutlined />} onClick={() => setRegisterModal(true)}>高级收录</Button>
              <Button
                icon={<UploadOutlined />}
                onClick={() => navigate(`${R.batch.integration}?action=file-import`)}
              >
                从本地文件建表
              </Button>
            </>
          )}
          <Button
            icon={<SearchOutlined />}
            loading={refreshing}
            onClick={() => { void loadCatalog({ userRefresh: true }) }}
          >
            刷新目录
          </Button>
        </Space>
      </div>
      {!canWrite && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="当前角色为只读：可浏览物理表目录与已收录字典；打开/展开自动收录与同步结构需具备「数据字典写」权限。"
        />
      )}
      <Alert
        type="info"
        showIcon
        closable
        style={{ marginBottom: 12 }}
        message={
          canWrite
            ? '目录默认可见（MySQL / Doris 可见库；可在数据源配置库白名单）。打开或展开未收录表即自动收录并同步字段注释；已收录表只读字典元数据。行数为库内估算值，结构变更后点「同步结构」，新建物理表后点「刷新目录」。'
            : '展示数据源账号可见库中的物理表。已收录表可查看字典与血缘；未收录表仅可浏览目录信息。'
        }
      />
      <Table
        dataSource={tables}
        columns={columns}
        rowKey="rowKey"
        className="dw-resizable-table"
        scroll={{ x: 900 }}
        tableLayout="fixed"
        loading={listLoading && !tables.length}
        expandable={{
          ...expandableControl,
          expandedRowRender: renderRowDetail,
          rowExpandable: (row: any) => !row.error,
        }}
      />

      <Modal
        title={`数据字典 - ${selectedTable?.qualified_name || selectedTable?.table_name}`}
        open={detailModal}
        onCancel={() => setDetailModal(false)}
        footer={null}
        width={900}
      >
        {selectedTable && (
          <Tabs
            onChange={(key) => {
              if (key === 'lineage') void ensureDetailExtra('lineage')
              if (key === 'impact') void ensureDetailExtra('impact')
            }}
            items={[
            {
              key: 'info', label: '基本信息',
              children: (
                <Descriptions column={2} bordered size="small">
                  <Descriptions.Item label="限定名" span={2}>{selectedTable.qualified_name || `${selectedTable.db_name}.${selectedTable.table_name}`}</Descriptions.Item>
                  <Descriptions.Item label="数据源">{selectedTable.datasource_name || '—'}</Descriptions.Item>
                  <Descriptions.Item label="类型">{selectedTable.ds_type || '—'}</Descriptions.Item>
                  <Descriptions.Item label="表名">{selectedTable.table_name}</Descriptions.Item>
                  <Descriptions.Item label="数据库/Catalog">{selectedTable.catalog || selectedTable.db_name}</Descriptions.Item>
                  <Descriptions.Item label="类型">{selectedTable.table_type}</Descriptions.Item>
                  <Descriptions.Item label="行数">{selectedTable.row_count}</Descriptions.Item>
                  <Descriptions.Item label="大小">{selectedTable.size_bytes ? `${(selectedTable.size_bytes / 1024 / 1024).toFixed(2)} MB` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="负责人">{selectedTable.owner}</Descriptions.Item>
                  <Descriptions.Item label="描述" span={2}>{selectedTable.table_comment}</Descriptions.Item>
                </Descriptions>
              )
            },
            {
              key: 'columns', label: `字段 (${selectedTable.columns?.length || 0})`,
              children: (
                <Table
                  dataSource={selectedTable.columns}
                  columns={colColumns}
                  rowKey="id"
                  size="small"
                  pagination={false}
                />
              )
            },
            {
              key: 'lineage', label: '血缘图谱',
              children: (
                <div>
                  {lineageLoading ? (
                    <div style={{ padding: 48, textAlign: 'center' }}><Spin tip="加载血缘…" /></div>
                  ) : (
                    <LineageGraph
                      data={lineageData}
                      currentTableId={selectedTable.id}
                      height={420}
                    />
                  )}
                </div>
              )
            },
            {
              key: 'impact', label: `影响分析${detailExtrasLoaded.impact ? ` (${impactData.length})` : ''}`,
              children: (
                <div>
                  {impactLoading ? (
                    <div style={{ padding: 48, textAlign: 'center' }}><Spin tip="加载影响分析…" /></div>
                  ) : (
                    <>
                      {impactData.length > 0 && (
                        <Alert
                          type="warning"
                          message={`该表变更将影响下游 ${impactData.length} 张表`}
                          style={{ marginBottom: 12 }}
                        />
                      )}
                      <Table
                        dataSource={impactData}
                        columns={impactColumns}
                        rowKey="table_name"
                        size="small"
                        pagination={false}
                      />
                    </>
                  )}
                </div>
              )
            },
            {
              key: 'preview', label: '数据预览',
              children: (
                <div>
                  <Button
                    type="primary"
                    size="small"
                    style={{ marginBottom: 12 }}
                    loading={previewLoading}
                    onClick={() => loadPreview(selectedTable.id)}
                  >
                    加载样例
                  </Button>
                  {previewLoading && <Spin />}
                  {previewData?.columns && (
                    <Table
                      dataSource={(previewData.rows || []).map((r: any, i: number) => ({ ...r, _key: i }))}
                      columns={(previewData.columns as string[]).map((c) => ({
                        title: c,
                        dataIndex: c,
                        ellipsis: true,
                        width: 140,
                        render: (v: unknown) => {
                          if (v === null || v === undefined || v === 'None') {
                            return <span style={{ color: '#bfbfbf' }}>NULL</span>
                          }
                          const text = formatCellDisplay(v)
                          return (
                            <span style={{ fontFamily: 'monospace', fontSize: 12 }} title={text.length > 80 ? text : undefined}>
                              {text}
                            </span>
                          )
                        },
                      }))}
                      rowKey="_key"
                      size="small"
                      scroll={{ x: true }}
                      pagination={{ pageSize: 20 }}
                    />
                  )}
                </div>
              )
            }
          ]} />
        )}
      </Modal>

      <Modal title="高级收录" open={registerModal} onOk={handleRegister} onCancel={() => setRegisterModal(false)} okText="收录">
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="datasource_id" label="数据源" rules={[{ required: true }]}>
            <Select options={datasources.map((d: any) => ({ label: d.name, value: d.id }))} />
          </Form.Item>
          <Form.Item name="db_name" label="数据库名"><Input /></Form.Item>
          <Form.Item name="table_name" label="表名" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="table_comment" label="描述"><Input /></Form.Item>
          <Form.Item name="owner" label="负责人"><Input /></Form.Item>
          <Form.Item name="tags" label="标签">
            <Select mode="tags" placeholder="输入标签后回车" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

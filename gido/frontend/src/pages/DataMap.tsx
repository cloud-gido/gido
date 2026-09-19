/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 *
 * 数据地图最终形态：左树（与 Studio「库表」同源 sqlSchemaCache）+ 右详（字典/血缘/影响/样例）。
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Table, Input, Button, Select, Tag, Space, message, Descriptions, Tabs, Alert, Spin, Form,
} from 'antd'
import { SyncOutlined, UploadOutlined, ReloadOutlined, CopyOutlined, StarOutlined, StarFilled } from '@ant-design/icons'
import { datamapApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { formatCellDisplay } from '../utils/cellDisplay'
import { classifyColumnType } from '../utils/columnTypeBadge'
import LineageGraph from '../components/LineageGraph'
import DataMapCatalogPanel, { type DataMapTableSelection } from '../components/DataMapCatalogPanel'
import { invalidateSqlSchemaCache } from '../utils/sqlSchemaCache'
import { tableKey } from '../utils/sqlSchemaTree'
import { R } from '../routes'
import { peekCachedDatasources, rememberDatasources } from '../utils/workspaceDatasource'
import { catalogCapableDatasources, isCatalogCapableDatasource } from '../utils/dataMapCatalogLoad'
import { pushRecent, readShortcuts, togglePin, type DataMapShortcut } from '../utils/dataMapPins'
import { queuePendingProbeScript } from '../utils/probePendingScript'
import '../components/DataMapCatalogPanel.css'

function usageRowsFromContext(res: any) {
  const rows: Array<{ key: string; kind: string; name: string; relation: string; peer: string; href?: string }> = []
  for (const item of res?.sql_tasks || []) {
    rows.push({
      key: `sql-${item.id}-${item.role}`,
      kind: 'SQL 任务',
      name: item.name,
      relation: item.role,
      peer: item.peer_table || '—',
      href: `${R.batch.studio}?node_id=${item.id}`,
    })
  }
  for (const item of res?.sync_tasks || []) {
    rows.push({
      key: `sync-${item.id}`,
      kind: '同步任务',
      name: item.name,
      relation: item.role,
      peer: item.peer_table || item.last_run_status || '—',
      href: R.batch.integration,
    })
  }
  for (const item of res?.quality_rules || []) {
    rows.push({
      key: `quality-${item.id}`,
      kind: '质量规则',
      name: item.name,
      relation: item.role,
      peer: item.active ? '启用' : '停用',
      href: R.batch.quality,
    })
  }
  for (const item of res?.data_apis || []) {
    rows.push({
      key: `api-${item.id}`,
      kind: '数据服务',
      name: item.name,
      relation: item.api_code || item.role,
      peer: item.status || '—',
      href: `${R.service.apis}?api_id=${item.id}`,
    })
  }
  for (const item of res?.stream_jobs || []) {
    rows.push({
      key: `stream-${item.id}`,
      kind: '实时作业',
      name: item.name,
      relation: item.role,
      peer: item.status || '—',
      href: `${R.stream.studio}?job_id=${item.id}`,
    })
  }
  return rows
}

function previewRecords(columns: string[], rows: unknown[]): Record<string, unknown>[] {
  return (rows || []).map((row, index) => {
    const record: Record<string, unknown> = { _key: index }
    if (Array.isArray(row)) {
      columns.forEach((column, columnIndex) => {
        record[column] = row[columnIndex]
      })
      return record
    }
    if (row && typeof row === 'object') {
      return { ...(row as Record<string, unknown>), _key: index }
    }
    return record
  })
}

function datamapErrMsg(e: any, fallback: string) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const status = e?.response?.status
  if (status === 403) return '无权限：只读角色可浏览库表；同步字段需具备数据字典写权限'
  if (status === 401) return '登录已失效，请重新登录后再试'
  return fallback
}

function regMapKey(catalog: string, table: string) {
  return `${String(catalog || '').trim()}|${String(table || '').trim()}`
}

export default function DataMapPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canWrite = can(user, P.GIDO_BATCH_DATAMAP_WRITE, currentWorkspace)
  const navigate = useNavigate()

  const [datasources, setDatasources] = useState<any[]>(() =>
    peekCachedDatasources(useAppStore.getState().currentWorkspace?.id),
  )
  const capableDs = useMemo(() => catalogCapableDatasources(datasources as any[]), [datasources])
  const [dsId, setDsId] = useState<number | undefined>(() => capableDs[0]?.id)
  const selectedDs = useMemo(() => capableDs.find((d) => d.id === dsId), [capableDs, dsId])

  const [registeredMap, setRegisteredMap] = useState<Map<string, number>>(new Map())
  const [refreshToken, setRefreshToken] = useState(0)
  const [selection, setSelection] = useState<DataMapTableSelection | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [selectedTable, setSelectedTable] = useState<any>(null)
  const [lineageData, setLineageData] = useState<{ nodes: any[]; edges: any[] }>({ nodes: [], edges: [] })
  const [impactData, setImpactData] = useState<any[]>([])
  const [lineageLoading, setLineageLoading] = useState(false)
  const [impactLoading, setImpactLoading] = useState(false)
  const [detailExtrasLoaded, setDetailExtrasLoaded] = useState<{ lineage?: boolean; impact?: boolean }>({})
  const [previewData, setPreviewData] = useState<any>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [usageRows, setUsageRows] = useState<ReturnType<typeof usageRowsFromContext>>([])
  const [usageLoading, setUsageLoading] = useState(false)
  const [usageLoaded, setUsageLoaded] = useState(false)
  const [searchHits, setSearchHits] = useState<any[]>([])
  const [searching, setSearching] = useState(false)
  const [searchValue, setSearchValue] = useState<string | undefined>()
  const [searchText, setSearchText] = useState('')
  const [columnQuery, setColumnQuery] = useState('')
  const [definition, setDefinition] = useState<any>(null)
  const [definitionLoading, setDefinitionLoading] = useState(false)
  const [openingProbe, setOpeningProbe] = useState(false)
  const [pins, setPins] = useState<DataMapShortcut[]>([])
  const [recents, setRecents] = useState<DataMapShortcut[]>([])
  const [bizSaving, setBizSaving] = useState(false)
  const [bizForm] = Form.useForm()
  const commentHealTriedRef = useRef<Set<number>>(new Set())
  const selectGenRef = useRef(0)
  const searchGenRef = useRef(0)
  const searchTimerRef = useRef<number | null>(null)

  const loadDatasources = useCallback(async () => {
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

  const loadRegistered = useCallback(async () => {
    if (!wsId) return
    try {
      const regRaw: any = await datamapApi.searchTables(wsId).catch(() => [])
      const registered = Array.isArray(regRaw) ? regRaw : []
      const m = new Map<string, number>()
      for (const t of registered) {
        if (dsId != null && Number(t.datasource_id) !== Number(dsId)) continue
        const cat = String(t.catalog || t.db_name || '').trim()
        const tn = String(t.table_name || '').trim()
        if (!tn || t.id == null) continue
        m.set(regMapKey(cat, tn), Number(t.id))
      }
      setRegisteredMap(m)
    } catch {
      setRegisteredMap(new Map())
    }
  }, [wsId, dsId])

  useEffect(() => { void loadDatasources() }, [loadDatasources])
  useEffect(() => { void loadRegistered() }, [loadRegistered])
  useEffect(() => {
    setPins(readShortcuts(wsId, 'pins'))
    setRecents(readShortcuts(wsId, 'recent'))
  }, [wsId])

  useEffect(() => {
    if (!capableDs.length) {
      setDsId(undefined)
      return
    }
    if (dsId == null || !capableDs.some((d) => d.id === dsId)) {
      setDsId(capableDs[0].id)
    }
  }, [capableDs, dsId])

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
      } catch { /* keep */ }
    })()
  }

  const openSelection = useCallback(async (sel: DataMapTableSelection) => {
    const gen = ++selectGenRef.current
    setSelection(sel)
    setDetailLoading(true)
    setSelectedTable(null)
    setLineageData({ nodes: [], edges: [] })
    setImpactData([])
    setDetailExtrasLoaded({})
    setPreviewData(null)
    setUsageRows([])
    setUsageLoaded(false)
    setColumnQuery('')
    setDefinition(null)
    try {
      let metaId = sel.metaTableId
      if (!metaId && canWrite && wsId) {
        const ensured: any = await datamapApi.ensureTable({
          workspace_id: wsId,
          datasource_id: sel.datasourceId,
          db_name: sel.catalog || undefined,
          table_name: sel.tableName,
          table_comment: sel.comment || undefined,
          table_type: 'table',
          sync_if_empty: true,
        })
        metaId = Number(ensured?.id) || undefined
        if (metaId) {
          setRegisteredMap((prev) => {
            const next = new Map(prev)
            next.set(regMapKey(sel.catalog, sel.tableName), metaId!)
            return next
          })
          setSelection((s) => (s && s.tableName === sel.tableName && s.catalog === sel.catalog
            ? { ...s, metaTableId: metaId, registered: true }
            : s))
        }
      }
      if (!metaId) {
        if (gen !== selectGenRef.current) return
        // 只读账号不写字典：直接展示引擎字段。有写权限时 openSelection 已在后台补齐字典行。
        const { fetchColumns } = await import('../utils/sqlSchemaCache')
        const cols = await fetchColumns(sel.datasourceId, sel.tableName, sel.catalog)
        setSelectedTable({
          id: null,
          datasource_id: sel.datasourceId,
          datasource_name: selectedDs?.name,
          catalog: sel.catalog,
          db_name: sel.catalog,
          table_name: sel.tableName,
          table_comment: sel.comment,
          qualified_name: `${selectedDs?.name || ''}.${sel.catalog}.${sel.tableName}`.replace(/^\./, ''),
          columns: cols.map((c, i) => ({
            id: i,
            name: c.name,
            type: c.type,
            comment: c.comment,
            nullable: c.nullable,
            primary_key: String(c.key || '').toUpperCase() === 'PRI',
          })),
          _physicalOnly: true,
        })
        if (wsId) {
          setRecents(pushRecent(wsId, {
            datasourceId: sel.datasourceId,
            catalog: sel.catalog,
            tableName: sel.tableName,
            comment: sel.comment,
          }))
        }
        return
      }
      const detail: any = await datamapApi.getTable(metaId)
      if (gen !== selectGenRef.current) return
      setSelectedTable(detail)
      if (wsId) {
        setRecents(pushRecent(wsId, {
          datasourceId: sel.datasourceId,
          catalog: sel.catalog,
          tableName: sel.tableName,
          comment: sel.comment || detail.table_comment,
          metaTableId: metaId,
        }))
      }
      scheduleCommentHeal(detail, (next) => {
        if (gen === selectGenRef.current) setSelectedTable(next)
      })
    } catch (e: any) {
      if (gen !== selectGenRef.current) return
      message.error(datamapErrMsg(e, '加载表详情失败'))
      setSelectedTable(null)
    } finally {
      if (gen === selectGenRef.current) setDetailLoading(false)
    }
  }, [canWrite, wsId, selectedDs?.name])

  const handleRefresh = async () => {
    if (dsId) invalidateSqlSchemaCache(dsId)
    await Promise.all([loadDatasources(), loadRegistered()])
    setRefreshToken((n) => n + 1)
    message.success('已刷新库表目录缓存')
  }

  const handleSyncSchema = async (tableId: number) => {
    if (!canWrite) {
      message.warning('当前为只读角色，无法同步结构')
      return
    }
    try {
      const res: any = await datamapApi.syncSchema(tableId)
      message.success(`同步成功，共 ${res.columns} 个字段`)
      const detail: any = await datamapApi.getTable(tableId)
      setSelectedTable(detail)
    } catch (e: any) {
      message.error(datamapErrMsg(e, '同步失败'))
    }
  }

  const ensureDetailExtra = async (key: 'lineage' | 'impact') => {
    if (!selectedTable?.id || detailExtrasLoaded[key]) return
    if (key === 'lineage') {
      setLineageLoading(true)
      try {
        const lineage: any = await datamapApi.getLineage(selectedTable.id, 3)
        setLineageData(lineage || { nodes: [], edges: [] })
        setDetailExtrasLoaded((prev) => ({ ...prev, lineage: true }))
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
      setDetailExtrasLoaded((prev) => ({ ...prev, impact: true }))
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

  const loadUsage = async (tableId: number) => {
    setUsageLoading(true)
    try {
      const res: any = await datamapApi.getContext(tableId)
      setUsageRows(usageRowsFromContext(res))
      setUsageLoaded(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载使用情况失败')
    } finally {
      setUsageLoading(false)
    }
  }

  const saveBusiness = async () => {
    if (!selectedTable?.id) return
    const values = await bizForm.validateFields()
    setBizSaving(true)
    try {
      const next: any = await datamapApi.updateBusiness(selectedTable.id, {
        owner: values.owner || '',
        tags: values.tags || [],
        business_description: values.business_description || '',
      })
      setSelectedTable(next)
      message.success('已保存业务信息')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally {
      setBizSaving(false)
    }
  }

  const onCatalogSearch = (value: string) => {
    if (searchTimerRef.current) window.clearTimeout(searchTimerRef.current)
    const q = value.trim()
    setSearchText(q)
    if (!wsId || q.length < 2) {
      setSearchHits([])
      setSearching(false)
      return
    }
    setSearching(true)
    const gen = ++searchGenRef.current
    searchTimerRef.current = window.setTimeout(() => {
      void datamapApi.catalog(wsId, { keyword: q })
        .then((raw: any) => {
          if (gen !== searchGenRef.current) return
          const list = (Array.isArray(raw) ? raw : []).filter((row) => row.table_name && !row.error).slice(0, 40)
          setSearchHits(list)
        })
        .catch(() => {
          if (gen === searchGenRef.current) setSearchHits([])
        })
        .finally(() => {
          if (gen === searchGenRef.current) setSearching(false)
        })
    }, 300)
  }

  useEffect(() => {
    if (!selectedTable?.id) return
    bizForm.setFieldsValue({
      owner: selectedTable.owner || undefined,
      tags: Array.isArray(selectedTable.tags) ? selectedTable.tags : [],
      business_description: selectedTable.business_description || '',
    })
  }, [bizForm, selectedTable])

  const colColumns = [
    { title: '字段名', dataIndex: 'name', width: 180, ellipsis: true, render: (name: string) => {
      const partitions = Array.isArray(definition?.partition_columns) ? definition.partition_columns : []
      const hit = partitions.some((column: string) => column.toLowerCase() === String(name || '').toLowerCase())
      return (
        <span>
          {name}
          {hit ? <Tag color="blue" style={{ marginLeft: 6 }}>分区</Tag> : null}
        </span>
      )
    } },
    {
      title: '类型',
      dataIndex: 'type',
      width: 160,
      ellipsis: true,
      render: (type: string) => {
        const badge = classifyColumnType(type)
        return (
          <span className="datamap-type-cell" title={type}>
            {badge ? <span className={`datamap-type-badge datamap-type-badge--${badge.kind}`}>{badge.badge}</span> : null}
            <span>{type || '—'}</span>
          </span>
        )
      },
    },
    { title: '描述', dataIndex: 'comment', ellipsis: true, render: (c: string) => c || <span style={{ color: '#bfbfbf' }}>—</span> },
    { title: '可空', dataIndex: 'nullable', width: 64, render: (v: boolean) => (v ? '是' : '否') },
    { title: '主键', dataIndex: 'primary_key', width: 64, render: (v: boolean) => (v ? <Tag color="gold">PK</Tag> : '') },
  ]

  const visibleColumns = useMemo(() => {
    const q = columnQuery.trim().toLowerCase()
    const cols = Array.isArray(selectedTable?.columns) ? selectedTable.columns : []
    if (!q) return cols
    return cols.filter((col: any) => {
      const haystack = `${col.name || ''} ${col.comment || ''} ${col.type || ''}`.toLowerCase()
      return haystack.includes(q)
    })
  }, [columnQuery, selectedTable])

  const openRelatedTable = (row: {
    id?: number
    table_id?: number
    datasource_id?: number
    db_name?: string
    table_name?: string
    table_comment?: string
  }) => {
    const tableName = row.table_name
    const datasourceId = Number(row.datasource_id)
    if (!tableName || !Number.isFinite(datasourceId)) return
    if (datasourceId !== dsId) setDsId(datasourceId)
    void openSelection({
      datasourceId,
      catalog: row.db_name || '',
      tableName,
      comment: row.table_comment,
      metaTableId: row.id || row.table_id,
      registered: true,
    })
  }

  const loadDefinition = async (tableId: number) => {
    setDefinitionLoading(true)
    try {
      const res: any = await datamapApi.getDefinition(tableId)
      setDefinition(res)
      return res
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '读取建表语句失败')
      return null
    } finally {
      setDefinitionLoading(false)
    }
  }

  const openInProbe = async () => {
    if (!selectedTable?.id) return
    setOpeningProbe(true)
    try {
      const def = definition || await loadDefinition(selectedTable.id)
      if (!def?.select_sql) return
      queuePendingProbeScript({
        name: selectedTable.table_name,
        sql: def.select_sql,
        datasourceId: selectedTable.datasource_id,
      })
      navigate(R.batch.probe)
    } finally {
      setOpeningProbe(false)
    }
  }

  const currentShortcut = (): DataMapShortcut | null => {
    if (!selectedTable || !selection) return null
    return {
      datasourceId: selection.datasourceId,
      catalog: selection.catalog,
      tableName: selection.tableName,
      comment: selectedTable.business_description || selectedTable.table_comment || selection.comment,
      metaTableId: selectedTable.id || undefined,
    }
  }

  const pinned = Boolean(currentShortcut() && pins.some((item) => (
    item.datasourceId === selection?.datasourceId
    && item.catalog === selection?.catalog
    && item.tableName === selection?.tableName
  )))

  const onTogglePin = () => {
    const pin = currentShortcut()
    if (!wsId || !pin) return
    setPins(togglePin(wsId, pin))
  }

  const openShortcut = (item: DataMapShortcut) => {
    if (item.datasourceId !== dsId) setDsId(item.datasourceId)
    void openSelection({
      datasourceId: item.datasourceId,
      catalog: item.catalog,
      tableName: item.tableName,
      comment: item.comment,
      metaTableId: item.metaTableId,
      registered: Boolean(item.metaTableId),
    })
  }

  const copyQualifiedName = async () => {
    const text = selectedTable?.qualified_name || `${selection?.catalog || ''}.${selection?.tableName || ''}`
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制表名')
    } catch {
      message.error('复制失败')
    }
  }

  const impactColumns = [
    { title: '数据库', dataIndex: 'db_name', render: (v: string) => v || '—' },
    {
      title: '下游表',
      dataIndex: 'table_name',
      render: (name: string, row: any) => (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => openRelatedTable(row)}>
          {name}
        </Button>
      ),
    },
    {
      title: '写入任务',
      dataIndex: 'task_name',
      render: (name: string, row: any) => {
        if (!name) return '—'
        const href = row.stream_job_id
          ? `${R.stream.studio}?job_id=${row.stream_job_id}`
          : row.task_node_id
            ? `${R.batch.studio}?node_id=${row.task_node_id}`
            : row.sync_task_id
              ? R.batch.integration
              : ''
        if (!href) return name
        return (
          <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate(href)}>
            {name}
          </Button>
        )
      },
    },
  ]

  const selectedTreeKey = selection
    ? tableKey(selection.catalog, selection.tableName)
    : null

  const physicalOnly = Boolean(selectedTable?._physicalOnly)

  return (
    <div className="datamap-page">
      <div className="datamap-page-toolbar">
        <h2 style={{ margin: 0 }}>数据地图</h2>
        <Space wrap size={8}>
          <Select
            placeholder="数据源"
            style={{ width: 200 }}
            value={dsId}
            onChange={(v) => {
              setDsId(v)
              setSelection(null)
              setSelectedTable(null)
              setSearchValue(undefined)
              setSearchHits([])
            }}
            options={capableDs.map((d: any) => ({ label: d.name, value: d.id }))}
            notFoundContent={datasources.length ? '无可枚举库表的数据源' : '加载中…'}
          />
          <Select
            showSearch
            allowClear
            filterOption={false}
            placeholder="搜索工作空间的表和字段"
            style={{ width: 260 }}
            value={searchValue}
            loading={searching}
            onSearch={onCatalogSearch}
            options={searchHits.map((row) => {
              const columnHit = Array.isArray(row.match_columns) && row.match_columns.length
                ? `字段 ${row.match_columns.join('、')}`
                : ''
              const bits = [row.datasource_name, `${row.catalog}.${row.table_name}`].filter(Boolean)
              if (columnHit) bits.push(columnHit)
              else if (row.table_comment) bits.push(row.table_comment)
              return { value: row.row_key, label: bits.join(' · ') }
            })}
            notFoundContent={searching ? <Spin size="small" /> : (searchText.length >= 2 ? '没有匹配的表' : '输入至少 2 个字符')}
            onChange={(key) => {
              setSearchValue(key)
              const row = searchHits.find((item) => item.row_key === key)
              if (!row) return
              if (row.datasource_id && row.datasource_id !== dsId) setDsId(row.datasource_id)
              void openSelection({
                datasourceId: row.datasource_id,
                catalog: row.catalog,
                tableName: row.table_name,
                comment: row.table_comment,
                metaTableId: row.meta_table_id || undefined,
                registered: Boolean(row.registered),
              })
            }}
          />
          {canWrite && (
            <Button
              icon={<UploadOutlined />}
              onClick={() => navigate(`${R.batch.integration}?action=file-import`)}
            >
              从本地文件建表
            </Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={() => { void handleRefresh() }}>
            刷新目录
          </Button>
        </Space>
      </div>

      {!canWrite && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12, flexShrink: 0 }}
          message="当前角色为只读：可浏览库表与字段。血缘和样例在有写权限的成员打开该表后可用。"
        />
      )}
      <Alert
        type="info"
        showIcon
        closable
        style={{ marginBottom: 12, flexShrink: 0 }}
        message={
          canWrite
            ? '左侧是数据源里的全部库表，与数据开发「库表」同源。单击表查看字段、血缘与样例。行数为估算值。共享集群可在数据源配置库白名单。'
            : '左侧浏览数据源里的全部库表。只读账号查看字段；血缘与样例在表被打开同步后可用。'
        }
      />

      <div className="datamap-page-body">
        <div className="datamap-page-tree">
          <DataMapCatalogPanel
            datasourceId={dsId}
            defaultCatalog={selectedDs?.database || null}
            registeredMap={registeredMap}
            selectedKey={selectedTreeKey}
            locateCatalog={selection?.catalog}
            locateTable={selection?.tableName}
            onSelectTable={(sel) => { void openSelection(sel) }}
            refreshToken={refreshToken}
          />
        </div>
        <div className="datamap-page-detail">
          {!selection && !detailLoading && (
            <div className="datamap-detail-empty">
              <div>
                <div style={{ marginBottom: 8, color: '#595959' }}>从左侧选择一张表，或搜索表名、字段、负责人</div>
                <div style={{ fontSize: 12 }}>
                  将展示字段注释、血缘、建表语句与样例
                  {isCatalogCapableDatasource(selectedDs) ? '' : '；请先选择 MySQL / Doris / PostgreSQL 数据源'}
                </div>
                {pins.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 12, marginBottom: 6 }}>收藏</div>
                    <Space wrap size={6}>
                      {pins.map((item) => (
                        <Button key={`pin-${item.datasourceId}-${item.catalog}-${item.tableName}`} size="small" onClick={() => openShortcut(item)}>
                          {item.catalog}.{item.tableName}
                        </Button>
                      ))}
                    </Space>
                  </div>
                )}
                {recents.length > 0 && (
                  <div style={{ marginTop: 16 }}>
                    <div style={{ fontSize: 12, marginBottom: 6 }}>最近浏览</div>
                    <Space wrap size={6}>
                      {recents.map((item) => (
                        <Button key={`recent-${item.datasourceId}-${item.catalog}-${item.tableName}`} size="small" type="text" onClick={() => openShortcut(item)}>
                          {item.catalog}.{item.tableName}
                        </Button>
                      ))}
                    </Space>
                  </div>
                )}
              </div>
            </div>
          )}
          {detailLoading && (
            <div className="datamap-detail-empty"><Spin tip="加载字典…" /></div>
          )}
          {!detailLoading && selectedTable && selection && (
            <>
              <div className="datamap-detail-header">
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div className="datamap-detail-title">
                    <span>{selectedTable.qualified_name || `${selection.catalog}.${selection.tableName}`}</span>
                    <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => { void copyQualifiedName() }} title="复制表名" />
                  </div>
                  <div className="datamap-detail-sub">
                    {selectedTable.datasource_name || selectedDs?.name || '数据源'}
                    {' · '}
                    {selectedTable.table_type || 'table'}
                    {selectedTable.row_count != null ? ` · 约 ${selectedTable.row_count} 行` : ''}
                  </div>
                  {(selectedTable.owner || (Array.isArray(selectedTable.tags) && selectedTable.tags.length > 0)) && (
                    <Space size={4} wrap style={{ marginTop: 8 }}>
                      {selectedTable.owner ? <Tag>负责人 {selectedTable.owner}</Tag> : null}
                      {(selectedTable.tags || []).map((tag: string) => <Tag key={tag} color="blue">{tag}</Tag>)}
                    </Space>
                  )}
                  {(selectedTable.business_description || selectedTable.table_comment) && (
                    <div className="datamap-detail-desc" title={selectedTable.business_description || selectedTable.table_comment}>
                      {selectedTable.business_description || selectedTable.table_comment}
                    </div>
                  )}
                </div>
                <Space>
                  <Button
                    icon={pinned ? <StarFilled /> : <StarOutlined />}
                    onClick={onTogglePin}
                  >
                    {pinned ? '已收藏' : '收藏'}
                  </Button>
                  {selectedTable.id && (
                    <Button loading={openingProbe} onClick={() => { void openInProbe() }}>
                      在数据探查中打开
                    </Button>
                  )}
                  {selectedTable.id && canWrite && (
                    <Button icon={<SyncOutlined />} onClick={() => handleSyncSchema(selectedTable.id)}>
                      同步结构
                    </Button>
                  )}
                </Space>
              </div>

              {selectedTable.business_description && selectedTable.table_comment && selectedTable.business_description !== selectedTable.table_comment && (
                <Alert type="info" showIcon style={{ marginBottom: 12 }} message={`引擎注释：${selectedTable.table_comment}`} />
              )}

              <Tabs
                onChange={(key) => {
                  if (key === 'lineage') void ensureDetailExtra('lineage')
                  if (key === 'impact') void ensureDetailExtra('impact')
                  if (key === 'preview' && selectedTable?.id) void loadPreview(selectedTable.id)
                  if (key === 'usage' && selectedTable?.id && !usageLoaded) void loadUsage(selectedTable.id)
                  if (key === 'ddl' && selectedTable?.id && !definition) void loadDefinition(selectedTable.id)
                }}
                items={[
                  {
                    key: 'columns',
                    label: columnQuery.trim()
                      ? `字段 (${visibleColumns.length}/${selectedTable.columns?.length || 0})`
                      : `字段 (${selectedTable.columns?.length || 0})`,
                    children: (
                      <>
                        <Input.Search
                          allowClear
                          placeholder="筛选字段名、类型或注释"
                          value={columnQuery}
                          onChange={(e) => setColumnQuery(e.target.value)}
                          style={{ marginBottom: 8, maxWidth: 360 }}
                        />
                        <Table
                          dataSource={visibleColumns}
                          columns={colColumns}
                          rowKey={(r: any) => r.id ?? r.name}
                          size="small"
                          pagination={false}
                          scroll={{ y: 'calc(100vh - 460px)' }}
                          locale={{ emptyText: columnQuery.trim() ? '没有匹配的字段' : '暂无字段' }}
                        />
                      </>
                    ),
                  },
                  {
                    key: 'ddl',
                    label: '建表语句',
                    disabled: physicalOnly,
                    children: physicalOnly ? null : (
                      definitionLoading ? (
                        <div style={{ padding: 48, textAlign: 'center' }}><Spin tip="读取建表语句…" /></div>
                      ) : (
                        <div>
                          {definition?.partitions ? (
                            <Alert
                              type="info"
                              showIcon
                              style={{ marginBottom: 12 }}
                              message={`分区 ${definition.partitions.count} 个${definition.partitions.method ? ` · ${definition.partitions.method}` : ''}${definition.partitions.expression ? ` · ${definition.partitions.expression}` : ''}`}
                              description={definition.partitions.names?.length
                                ? `分区：${definition.partitions.names.join('、')}${definition.partitions.count > definition.partitions.names.length ? ' 等' : ''}`
                                : undefined}
                            />
                          ) : (
                            <div style={{ marginBottom: 12, color: '#8c8c8c', fontSize: 12 }}>
                              {definition ? '这张表没有分区。' : '打开后显示引擎里的建表语句和分区。'}
                            </div>
                          )}
                          {definition?.engine && (
                            <div style={{ marginBottom: 8, fontSize: 12, color: '#8c8c8c' }}>引擎 {definition.engine}</div>
                          )}
                          {definition?.ddl_note && (
                            <div style={{ marginBottom: 8, fontSize: 12, color: '#8c8c8c' }}>{definition.ddl_note}</div>
                          )}
                          <pre className="datamap-ddl">{definition?.ddl || '—'}</pre>
                          <Space style={{ marginTop: 12 }}>
                            <Button
                              size="small"
                              onClick={() => {
                                const text = definition?.ddl || ''
                                if (!text) return
                                void navigator.clipboard.writeText(text).then(
                                  () => message.success('已复制建表语句'),
                                  () => message.error('复制失败'),
                                )
                              }}
                            >
                              复制建表语句
                            </Button>
                            <Button size="small" loading={openingProbe} onClick={() => { void openInProbe() }}>
                              在数据探查中打开
                            </Button>
                          </Space>
                        </div>
                      )
                    ),
                  },
                  {
                    key: 'info',
                    label: '基本信息',
                    children: (
                      <>
                        <Descriptions column={2} bordered size="small">
                          <Descriptions.Item label="限定名" span={2}>
                            {selectedTable.qualified_name || `${selectedTable.db_name}.${selectedTable.table_name}`}
                          </Descriptions.Item>
                          <Descriptions.Item label="数据源">{selectedTable.datasource_name || selectedDs?.name || '—'}</Descriptions.Item>
                          <Descriptions.Item label="类型">{selectedTable.ds_type || selectedDs?.ds_type || '—'}</Descriptions.Item>
                          <Descriptions.Item label="表名">{selectedTable.table_name}</Descriptions.Item>
                          <Descriptions.Item label="数据库/Catalog">{selectedTable.catalog || selectedTable.db_name}</Descriptions.Item>
                          <Descriptions.Item label="行数">{selectedTable.row_count ?? '—'}</Descriptions.Item>
                          <Descriptions.Item label="引擎注释" span={2}>{selectedTable.table_comment || '—'}</Descriptions.Item>
                        </Descriptions>
                        {selectedTable.id && canWrite ? (
                          <Form form={bizForm} layout="vertical" style={{ marginTop: 16, maxWidth: 640 }} onFinish={() => { void saveBusiness() }}>
                            <Form.Item name="owner" label="负责人">
                              <Input maxLength={64} placeholder="谁负责这张表" />
                            </Form.Item>
                            <Form.Item name="tags" label="标签">
                              <Select mode="tags" placeholder="业务标签，回车添加" tokenSeparators={[',']} />
                            </Form.Item>
                            <Form.Item name="business_description" label="业务说明">
                              <Input.TextArea
                                rows={4}
                                maxLength={4000}
                                showCount
                                placeholder="这张表是什么、给谁用。同步结构不会覆盖这里。"
                              />
                            </Form.Item>
                            <Button type="primary" htmlType="submit" loading={bizSaving}>保存</Button>
                          </Form>
                        ) : (
                          <Descriptions column={2} bordered size="small" style={{ marginTop: 12 }}>
                            <Descriptions.Item label="负责人">{selectedTable.owner || '—'}</Descriptions.Item>
                            <Descriptions.Item label="标签">
                              {Array.isArray(selectedTable.tags) && selectedTable.tags.length
                                ? selectedTable.tags.map((tag: string) => <Tag key={tag}>{tag}</Tag>)
                                : '—'}
                            </Descriptions.Item>
                            <Descriptions.Item label="业务说明" span={2}>{selectedTable.business_description || '—'}</Descriptions.Item>
                          </Descriptions>
                        )}
                      </>
                    ),
                  },
                  {
                    key: 'usage',
                    label: usageLoaded ? `使用 (${usageRows.length})` : '使用',
                    disabled: physicalOnly,
                    children: physicalOnly ? null : (
                      usageLoading ? (
                        <div style={{ padding: 48, textAlign: 'center' }}><Spin tip="加载使用情况…" /></div>
                      ) : (
                        <Table
                          dataSource={usageRows}
                          columns={[
                            { title: '类型', dataIndex: 'kind', width: 110 },
                            { title: '名称', dataIndex: 'name', ellipsis: true, render: (name: string, row: any) => (
                              row.href
                                ? <Button type="link" size="small" style={{ padding: 0 }} onClick={() => navigate(row.href)}>{name}</Button>
                                : name
                            ) },
                            { title: '关系', dataIndex: 'relation', width: 140, ellipsis: true },
                            { title: '关联', dataIndex: 'peer', ellipsis: true },
                          ]}
                          rowKey="key"
                          size="small"
                          pagination={false}
                          locale={{ emptyText: '还没有 SQL 任务、同步、实时作业、质量规则或数据服务引用这张表。' }}
                        />
                      )
                    ),
                  },
                  {
                    key: 'lineage',
                    label: '血缘图谱',
                    disabled: physicalOnly,
                    children: physicalOnly ? null : (
                      lineageLoading ? (
                        <div style={{ padding: 48, textAlign: 'center' }}><Spin tip="加载血缘…" /></div>
                      ) : (
                        <LineageGraph
                          data={lineageData}
                          currentTableId={selectedTable.id}
                          height={420}
                          onNodeClick={(id) => {
                            const node = lineageData.nodes.find((item) => Number(item.id) === id)
                            if (!node || Number(node.id) === Number(selectedTable.id)) return
                            openRelatedTable(node)
                          }}
                        />
                      )
                    ),
                  },
                  {
                    key: 'impact',
                    label: `影响分析${detailExtrasLoaded.impact ? ` (${impactData.length})` : ''}`,
                    disabled: physicalOnly,
                    children: physicalOnly ? null : (
                      impactLoading ? (
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
                            rowKey={(row: any) => `${row.table_id}-${row.task_node_id || ''}`}
                            size="small"
                            pagination={false}
                            locale={{ emptyText: '暂无下游。没有 SQL 任务把这张表写进别的表。' }}
                          />
                        </>
                      )
                    ),
                  },
                  {
                    key: 'preview',
                    label: '数据预览',
                    disabled: physicalOnly,
                    children: physicalOnly ? null : (
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
                            dataSource={previewRecords(previewData.columns as string[], previewData.rows || [])}
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
                    ),
                  },
                ]}
              />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

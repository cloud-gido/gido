/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Table, Input, Button, Modal, Form, Select, Tag, Space, message, Descriptions, Tabs, Alert, Spin } from 'antd'
import { SearchOutlined, PlusOutlined, SyncOutlined, ApartmentOutlined, TableOutlined, UploadOutlined } from '@ant-design/icons'
import { datamapApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { formatCellDisplay } from '../utils/cellDisplay'
import LineageGraph from '../components/LineageGraph'
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
  if (status === 403) return '无权限：数据分析等只读角色可浏览字典，注册/同步需具备数据字典写权限'
  if (status === 401) return '登录已失效，请重新登录后再试'
  return fallback
}

export default function DataMapPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canWrite = can(user, P.GIDO_BATCH_DATAMAP_WRITE, currentWorkspace)
  const navigate = useNavigate()
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
  const loadGenRef = useRef(0)
  const keywordRef = useRef(keyword)
  keywordRef.current = keyword
  const dsFilterRef = useRef(dsFilter)
  dsFilterRef.current = dsFilter

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

  const openDetail = async (table: any) => {
    try {
      const detail: any = await datamapApi.getTable(table.id)
      setSelectedTable(detail)
      setLineageData({ nodes: [], edges: [] })
      setImpactData([])
      setDetailExtrasLoaded({})
      setPreviewData(null)
      setDetailModal(true)
    } catch (e: any) {
      message.error(datamapErrMsg(e, '加载字典失败'))
    }
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
    } catch (e: any) {
      message.error(datamapErrMsg(e, '同步失败'))
    }
  }

  const handleRegister = async () => {
    if (!canWrite) {
      message.warning('当前为只读角色，无法注册表')
      return
    }
    try {
      const values = await form.validateFields()
      values.workspace_id = wsId
      const created: any = await datamapApi.registerTable(values)
      if (created?.sync_warning) {
        message.warning(`已注册，但字段同步失败：${created.sync_warning}`)
      } else {
        message.success(`注册成功，已同步 ${created?.columns_synced ?? created?.columns?.length ?? 0} 个字段`)
      }
      setRegisterModal(false)
      await load()
      if (created?.id) openDetail({ id: created.id })
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(datamapErrMsg(e, '注册失败'))
    }
  }

  const openCatalogRow = async (row: any) => {
    if (row.error) {
      message.warning(`数据源 ${row.datasource_name} 拉表失败：${row.error}`)
      return
    }
    if (row.registered && row.meta_table_id) {
      openDetail({ id: row.meta_table_id })
      return
    }
    if (!canWrite) {
      message.warning('当前为只读角色，可浏览物理表目录，注册需具备「数据字典写」权限')
      return
    }
    Modal.confirm({
      title: '注册到数据地图',
      content: `将「${row.qualified_name}」注册为元数据后自动同步字段，并可查看血缘、样例数据。`,
      okText: '注册并打开',
      onOk: async () => {
        try {
          const created: any = await datamapApi.registerTable({
            workspace_id: wsId,
            datasource_id: row.datasource_id,
            db_name: row.catalog,
            table_name: row.table_name,
            table_comment: row.table_comment || undefined,
            table_type: String(row.table_type || 'table').toLowerCase().includes('view') ? 'view' : 'table',
          })
          if (created?.sync_warning) {
            message.warning(`已注册，但字段同步失败：${created.sync_warning}`)
          } else {
            message.success(`已注册并同步 ${created?.columns_synced ?? created?.columns?.length ?? 0} 个字段`)
          }
          await load()
          if (created?.id) openDetail({ id: created.id })
        } catch (e: any) {
          message.error(datamapErrMsg(e, '注册失败'))
          throw e
        }
      },
    })
  }

  const columns = [
    {
      title: '限定名（数据源.库.表）',
      dataIndex: 'qualified_name',
      ellipsis: true,
      render: (q: string, row: any) => (
        row.registered || canWrite
          ? <a onClick={() => openCatalogRow(row)}>{q}</a>
          : <span>{q}</span>
      ),
    },
    { title: '数据源', dataIndex: 'datasource_name', width: 110 },
    { title: '库', dataIndex: 'catalog', width: 100 },
    { title: '表', dataIndex: 'table_name', width: 140, ellipsis: true },
    {
      title: '注册',
      dataIndex: 'registered',
      width: 72,
      render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '已注册' : '未注册'}</Tag>,
    },
    { title: '描述', dataIndex: 'table_comment', ellipsis: true },
    { title: '类型', dataIndex: 'table_type', width: 80, render: (t: string) => t ? <Tag>{t}</Tag> : '—' },
    { title: '行数', dataIndex: 'row_count', width: 88, render: (n: number) => n ?? '—' },
    {
      title: '操作', width: 220, render: (_: any, row: any) => (
        <Space>
          {row.meta_table_id && (
            <>
              {canWrite && (
                <Button size="small" icon={<SyncOutlined />} onClick={(e) => { e.stopPropagation(); handleSyncSchema(row.meta_table_id) }}>同步结构</Button>
              )}
              <Button size="small" icon={<ApartmentOutlined />} onClick={(e) => { e.stopPropagation(); openDetail({ id: row.meta_table_id }) }}>字典</Button>
            </>
          )}
          {!row.registered && !row.error && canWrite && (
            <Button size="small" type="link" onClick={(e) => { e.stopPropagation(); openCatalogRow(row) }}>注册</Button>
          )}
        </Space>
      )
    }
  ]

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
            placeholder="搜索表名/描述"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onSearch={() => { void loadCatalog({ userRefresh: true }) }}
            style={{ width: 260 }}
          />
          {canWrite && (
            <>
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setRegisterModal(true)}>手动注册表</Button>
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
          message="当前角色为只读：可浏览物理表目录与已注册字典，注册/同步结构需具备「数据字典写」权限（如数据开发、管理员）。"
        />
      )}
      <Alert
        type="info"
        showIcon
        closable
        style={{ marginBottom: 12 }}
        message={
          canWrite
            ? '展示已启用数据源中可枚举的物理表（MySQL / Doris / PostgreSQL）。注册时自动同步字段；结构变更后点「同步结构」，新建表后点「刷新目录」。'
            : '展示已启用数据源中可枚举的物理表。已注册表可查看字典与血缘；未注册表仅可浏览目录信息。'
        }
      />
      <Table
        dataSource={tables}
        columns={columns}
        rowKey="rowKey"
        scroll={{ x: 1100 }}
        loading={listLoading && !tables.length}
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
                    icon={<TableOutlined />}
                    onClick={() => loadPreview(selectedTable.id)}
                    loading={previewLoading}
                    style={{ marginBottom: 12 }}
                  >
                    加载数据
                  </Button>
                  {previewData && (
                    <Table
                      dataSource={previewData.rows.map((r: any[], i: number) => {
                        const obj: any = { _key: i }
                        previewData.columns.forEach((c: string, ci: number) => { obj[c] = r[ci] })
                        return obj
                      })}
                      columns={previewData.columns.map((c: string) => ({
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

      <Modal title="注册表" open={registerModal} onOk={handleRegister} onCancel={() => setRegisterModal(false)}>
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

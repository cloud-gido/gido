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
  Table, Input, Button, Modal, Form, Select, Tag, Space, message, Descriptions, Tabs, Alert, Spin,
} from 'antd'
import { PlusOutlined, SyncOutlined, UploadOutlined, ReloadOutlined } from '@ant-design/icons'
import { datamapApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { formatCellDisplay } from '../utils/cellDisplay'
import LineageGraph from '../components/LineageGraph'
import DataMapCatalogPanel, { type DataMapTableSelection } from '../components/DataMapCatalogPanel'
import { invalidateSqlSchemaCache } from '../utils/sqlSchemaCache'
import { tableKey } from '../utils/sqlSchemaTree'
import { R } from '../routes'
import { peekCachedDatasources, rememberDatasources } from '../utils/workspaceDatasource'
import { catalogCapableDatasources, isCatalogCapableDatasource } from '../utils/dataMapCatalogLoad'
import './DataMapCatalogPanel.css'

function datamapErrMsg(e: any, fallback: string) {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const status = e?.response?.status
  if (status === 403) return '无权限：只读角色可浏览目录；收录/同步需具备数据字典写权限'
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
  const [registerModal, setRegisterModal] = useState(false)
  const [form] = Form.useForm()
  const commentHealTriedRef = useRef<Set<number>>(new Set())
  const selectGenRef = useRef(0)

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
        // 只读且未收录：用物理列即时展示（不落库）
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
        return
      }
      const detail: any = await datamapApi.getTable(metaId)
      if (gen !== selectGenRef.current) return
      setSelectedTable(detail)
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
      await loadRegistered()
      if (created?.id && values.datasource_id && values.table_name) {
        void openSelection({
          datasourceId: values.datasource_id,
          catalog: values.db_name || '',
          tableName: values.table_name,
          comment: values.table_comment,
          metaTableId: created.id,
          registered: true,
        })
      }
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(datamapErrMsg(e, '收录失败'))
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

  const colColumns = [
    { title: '字段名', dataIndex: 'name', width: 160, ellipsis: true },
    { title: '类型', dataIndex: 'type', width: 120, ellipsis: true },
    { title: '描述', dataIndex: 'comment', ellipsis: true, render: (c: string) => c || <span style={{ color: '#bfbfbf' }}>—</span> },
    { title: '可空', dataIndex: 'nullable', width: 64, render: (v: boolean) => (v ? '是' : '否') },
    { title: '主键', dataIndex: 'primary_key', width: 64, render: (v: boolean) => (v ? <Tag color="gold">PK</Tag> : '') },
  ]

  const impactColumns = [
    { title: '数据库', dataIndex: 'db_name' },
    { title: '表名', dataIndex: 'table_name' },
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
            }}
            options={capableDs.map((d: any) => ({ label: d.name, value: d.id }))}
            notFoundContent={datasources.length ? '无可枚举库表的数据源' : '加载中…'}
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
          message="当前角色为只读：可浏览物理库表树与已收录字典；单击表自动收录与同步结构需「数据字典写」权限。"
        />
      )}
      <Alert
        type="info"
        showIcon
        closable
        style={{ marginBottom: 12, flexShrink: 0 }}
        message={
          canWrite
            ? '左侧库表树与数据开发「库表」同源（可见库懒加载）。单击表即打开右侧字典并自动收录；行数为估算值。共享集群可在数据源配置库白名单。'
            : '左侧浏览物理库表；已收录表可查看血缘与样例。未收录表仅展示实时字段（不落库）。'
        }
      />

      <div className="datamap-page-body">
        <div className="datamap-page-tree">
          <DataMapCatalogPanel
            datasourceId={dsId}
            defaultCatalog={selectedDs?.database || null}
            registeredMap={registeredMap}
            selectedKey={selectedTreeKey}
            onSelectTable={(sel) => { void openSelection(sel) }}
            refreshToken={refreshToken}
          />
        </div>
        <div className="datamap-page-detail">
          {!selection && !detailLoading && (
            <div className="datamap-detail-empty">
              <div>
                <div style={{ marginBottom: 8, color: '#595959' }}>从左侧选择一张表</div>
                <div style={{ fontSize: 12 }}>
                  将展示字段注释、血缘与样例
                  {isCatalogCapableDatasource(selectedDs) ? '' : '；请先选择 MySQL / Doris / PostgreSQL 数据源'}
                </div>
              </div>
            </div>
          )}
          {detailLoading && (
            <div className="datamap-detail-empty"><Spin tip="加载字典…" /></div>
          )}
          {!detailLoading && selectedTable && selection && (
            <>
              <div className="datamap-detail-header">
                <div>
                  <div className="datamap-detail-title">
                    {selectedTable.qualified_name || `${selection.catalog}.${selection.tableName}`}
                  </div>
                  <div className="datamap-detail-sub">
                    {physicalOnly ? (
                      <Tag>目录（未收录）</Tag>
                    ) : (
                      <Tag color="green">已收录</Tag>
                    )}
                    <span style={{ marginLeft: 8 }}>
                      {selectedTable.table_type || 'table'}
                      {selectedTable.row_count != null ? ` · 约 ${selectedTable.row_count} 行` : ''}
                    </span>
                  </div>
                </div>
                <Space>
                  {!physicalOnly && selectedTable.id && canWrite && (
                    <Button icon={<SyncOutlined />} onClick={() => handleSyncSchema(selectedTable.id)}>
                      同步结构
                    </Button>
                  )}
                  {physicalOnly && canWrite && (
                    <Button
                      type="primary"
                      onClick={() => void openSelection({ ...selection, metaTableId: undefined, registered: false })}
                    >
                      收录到字典
                    </Button>
                  )}
                </Space>
              </div>

              {selectedTable.table_comment && (
                <Alert type="info" showIcon style={{ marginBottom: 12 }} message={selectedTable.table_comment} />
              )}

              <Tabs
                onChange={(key) => {
                  if (physicalOnly && (key === 'lineage' || key === 'impact' || key === 'preview')) {
                    message.info('收录到字典后可使用血缘 / 影响分析 / 样例预览')
                    return
                  }
                  if (key === 'lineage') void ensureDetailExtra('lineage')
                  if (key === 'impact') void ensureDetailExtra('impact')
                }}
                items={[
                  {
                    key: 'columns',
                    label: `字段 (${selectedTable.columns?.length || 0})`,
                    children: (
                      <Table
                        dataSource={selectedTable.columns}
                        columns={colColumns}
                        rowKey={(r: any) => r.id ?? r.name}
                        size="small"
                        pagination={false}
                        scroll={{ y: 'calc(100vh - 420px)' }}
                      />
                    ),
                  },
                  {
                    key: 'info',
                    label: '基本信息',
                    children: (
                      <Descriptions column={2} bordered size="small">
                        <Descriptions.Item label="限定名" span={2}>
                          {selectedTable.qualified_name || `${selectedTable.db_name}.${selectedTable.table_name}`}
                        </Descriptions.Item>
                        <Descriptions.Item label="数据源">{selectedTable.datasource_name || selectedDs?.name || '—'}</Descriptions.Item>
                        <Descriptions.Item label="类型">{selectedTable.ds_type || selectedDs?.ds_type || '—'}</Descriptions.Item>
                        <Descriptions.Item label="表名">{selectedTable.table_name}</Descriptions.Item>
                        <Descriptions.Item label="数据库/Catalog">{selectedTable.catalog || selectedTable.db_name}</Descriptions.Item>
                        <Descriptions.Item label="行数">{selectedTable.row_count ?? '—'}</Descriptions.Item>
                        <Descriptions.Item label="负责人">{selectedTable.owner || '—'}</Descriptions.Item>
                        <Descriptions.Item label="描述" span={2}>{selectedTable.table_comment || '—'}</Descriptions.Item>
                      </Descriptions>
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
                            rowKey="table_name"
                            size="small"
                            pagination={false}
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
                    ),
                  },
                ]}
              />
            </>
          )}
        </div>
      </div>

      <Modal title="高级收录" open={registerModal} onOk={handleRegister} onCancel={() => setRegisterModal(false)} okText="收录">
        <Form form={form} layout="vertical" style={{ marginTop: 16 }} initialValues={{ datasource_id: dsId }}>
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

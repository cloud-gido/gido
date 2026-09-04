/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Table, Input, Button, Modal, Form, Select, Tag, Space, message, Descriptions, Tabs, Alert, Spin } from 'antd'
import { SearchOutlined, PlusOutlined, SyncOutlined, ApartmentOutlined, TableOutlined, UploadOutlined } from '@ant-design/icons'
import { datamapApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { formatCellDisplay } from '../utils/cellDisplay'
import LineageGraph from '../components/LineageGraph'
import { R } from '../routes'

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
  const [tables, setTables] = useState<any[]>([])
  const [datasources, setDatasources] = useState<any[]>([])
  const [keyword, setKeyword] = useState('')
  const [dsFilter, setDsFilter] = useState<number | undefined>(undefined)
  const [detailModal, setDetailModal] = useState(false)
  const [selectedTable, setSelectedTable] = useState<any>(null)
  const [lineageData, setLineageData] = useState<{ nodes: any[], edges: any[] }>({ nodes: [], edges: [] })
  const [impactData, setImpactData] = useState<any[]>([])
  const [previewData, setPreviewData] = useState<any>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [registerModal, setRegisterModal] = useState(false)
  const [form] = Form.useForm()

  const load = async () => {
    if (!wsId) return
    const [c, d, regRaw]: any = await Promise.all([
      datamapApi.catalog(wsId, { datasource_id: dsFilter, keyword: keyword || undefined }).catch(() => []),
      datasourceApi.list(wsId).catch(() => []),
      datamapApi.searchTables(wsId, keyword || undefined).catch(() => []),
    ])
    const catalogRows = Array.isArray(c) ? c : []
    const registered = Array.isArray(regRaw) ? regRaw : []
    const keyOf = (dsid: number | string, cat: string, tn: string) =>
      `${Number(dsid)}|${String(cat || '').trim()}|${String(tn || '').trim()}`
    const seen = new Set<string>()
    for (const x of catalogRows) {
      if (x.error || x.datasource_id == null) continue
      seen.add(keyOf(x.datasource_id, x.catalog || '', x.table_name || ''))
    }
    const extras: any[] = []
    for (const t of registered) {
      if (dsFilter != null && t.datasource_id !== dsFilter) continue
      const cat = String(t.catalog || t.db_name || '')
      const tn = String(t.table_name || '')
      if (!tn) continue
      const k = keyOf(t.datasource_id, cat, tn)
      if (seen.has(k)) continue
      seen.add(k)
      extras.push({
        row_key: `reg-${t.id}-${k}`,
        registered: true,
        meta_table_id: t.id,
        datasource_id: t.datasource_id,
        datasource_name: t.datasource_name || '—',
        catalog: cat || null,
        table_name: tn,
        qualified_name: t.qualified_name || k,
        table_comment: t.table_comment,
        table_type: t.table_type,
        row_count: t.row_count,
        tags: t.tags,
        owner: t.owner,
        last_updated: t.last_updated,
      })
    }
    const merged = [...catalogRows, ...extras].map((row: any, i: number) => ({
      ...row,
      rowKey: row.row_key ?? row.rowKey ?? `row-${i}`,
    }))
    setTables(merged)
    setDatasources(d as unknown as any[])
  }

  useEffect(() => { load() }, [wsId, dsFilter])

  const openDetail = async (table: any) => {
    const [detail, lineage, impact]: any = await Promise.all([
      datamapApi.getTable(table.id),
      datamapApi.getLineage(table.id, 3),
      datamapApi.getImpact(table.id)
    ])
    setSelectedTable(detail)
    setLineageData(lineage)
    setImpactData(impact.impacted_tables || [])
    setPreviewData(null)
    setDetailModal(true)
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
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <h2 style={{ margin: 0 }}>数据地图</h2>
        <Space wrap>
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
            onSearch={load}
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
          <Button icon={<SearchOutlined />} onClick={load}>刷新目录</Button>
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
        style={{ marginBottom: 12 }}
        message={
          canWrite
            ? '展示当前工作空间内已启用数据源中可枚举的物理表（MySQL / Doris / PostgreSQL；未注册也可浏览）。注册时会自动同步字段到数据字典；表结构变更后可点「同步结构」刷新；新建物理表后请点「刷新目录」。'
            : '展示当前工作空间内已启用数据源中可枚举的物理表。点击已注册表可查看字典与血缘；未注册表仅可浏览目录信息。'
        }
      />
      <Table dataSource={tables} columns={columns} rowKey="rowKey" scroll={{ x: 1100 }} />

      <Modal
        title={`数据字典 - ${selectedTable?.qualified_name || selectedTable?.table_name}`}
        open={detailModal}
        onCancel={() => setDetailModal(false)}
        footer={null}
        width={900}
      >
        {selectedTable && (
          <Tabs items={[
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
                  <LineageGraph
                    data={lineageData}
                    currentTableId={selectedTable.id}
                    height={420}
                  />
                </div>
              )
            },
            {
              key: 'impact', label: `影响分析 (${impactData.length})`,
              children: (
                <div>
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

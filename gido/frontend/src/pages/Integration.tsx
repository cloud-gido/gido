/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Table, Button, Modal, Form, Input, Select, Tag, Space, message, Drawer, Switch, Alert,
  Tabs, InputNumber, Popconfirm, Tooltip,
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, DeleteOutlined, EditOutlined, HistoryOutlined,
  CheckCircleOutlined, ReloadOutlined,
} from '@ant-design/icons'
import { integrationApi, datasourceApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import CronBuilder from '../components/CronBuilder'

type FieldMapping = { src: string; dst: string }

const SYNC_MODES = [
  { label: '全量（先清空目标表）', value: 'full' },
  { label: '增量（按字段水位）', value: 'incremental' },
  { label: 'CDC / 准实时（后台轮询增量）', value: 'cdc' },
]

const STATUS_COLOR: Record<string, string> = {
  success: 'success',
  failed: 'error',
  running: 'processing',
}

export default function IntegrationPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canWrite = can(user, P.GIDO_BATCH_INTEGRATION_WRITE, currentWorkspace)
  const canRun = can(user, P.GIDO_BATCH_INTEGRATION_RUN, currentWorkspace)

  const [tasks, setTasks] = useState<any[]>([])
  const [datasources, setDatasources] = useState<any[]>([])
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyTask, setHistoryTask] = useState<any>(null)
  const [records, setRecords] = useState<any[]>([])
  const [form] = Form.useForm()
  const [srcTables, setSrcTables] = useState<{ label: string; value: string }[]>([])
  const [dstTables, setDstTables] = useState<{ label: string; value: string }[]>([])
  const [srcCols, setSrcCols] = useState<any[]>([])
  const [mappings, setMappings] = useState<FieldMapping[]>([])
  const [cdcStatusMap, setCdcStatusMap] = useState<Record<number, any>>({})
  const pollRef = useRef<number | null>(null)

  const integrationDs = useMemo(
    () => datasources.filter((d: any) => ['mysql', 'doris', 'postgresql'].includes((d.ds_type || '').toLowerCase())),
    [datasources],
  )

  const load = async () => {
    if (!wsId) return
    const [t, d]: any = await Promise.all([integrationApi.listTasks(wsId), datasourceApi.list(wsId)])
    const list = Array.isArray(t) ? t : []
    setTasks(list)
    setDatasources(Array.isArray(d) ? d : [])
    const cdcTasks = list.filter((x: any) => x.sync_mode === 'cdc')
    const st: Record<number, any> = {}
    await Promise.all(
      cdcTasks.map(async (x: any) => {
        try {
          st[x.id] = await integrationApi.cdcStatus(x.id)
        } catch { /* ignore */ }
      }),
    )
    setCdcStatusMap(st)
  }

  useEffect(() => { load() }, [wsId])

  const dsOptions = integrationDs.map((d: any) => ({
    label: `${d.name} (${d.ds_type})`,
    value: d.id,
  }))

  const loadTables = async (dsId: number | undefined, side: 'src' | 'dst') => {
    if (!dsId) return
    try {
      const res: any = await integrationApi.listTables(dsId)
      const opts = (res?.tables || []).map((t: any) => ({ label: t.name, value: t.name }))
      if (side === 'src') setSrcTables(opts)
      else setDstTables(opts)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载表列表失败')
    }
  }

  const loadSrcColumns = async (dsId?: number, table?: string) => {
    if (!dsId || !table) return
    try {
      const res: any = await integrationApi.getColumns(dsId, table)
      setSrcCols(res?.columns || [])
    } catch {
      setSrcCols([])
    }
  }

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    setMappings([])
    setSrcCols([])
    setSrcTables([])
    setDstTables([])
    const wh =
      currentWorkspace?.effective_warehouse_datasource_id ??
      currentWorkspace?.warehouse_datasource_id ??
      currentWorkspace?.default_datasource_id
    form.setFieldsValue({
      sync_mode: 'full',
      is_active: true,
      batch_size: 2000,
      schedule_cron: '',
      dst_datasource_id: wh,
      src_datasource_id: currentWorkspace?.default_datasource_id,
    })
    setDrawerOpen(true)
  }

  const openEdit = async (row: any) => {
    setEditingId(row.id)
    const detail: any = await integrationApi.getTask(row.id)
    const cfg = detail.sync_config || {}
    setMappings(cfg.field_mappings || [])
    form.setFieldsValue({
      name: detail.name,
      description: detail.description,
      src_datasource_id: detail.src_datasource_id,
      dst_datasource_id: detail.dst_datasource_id,
      src_table: detail.src_table,
      dst_table: detail.dst_table,
      sync_mode: detail.sync_mode,
      schedule_cron: detail.schedule_cron || '',
      is_active: detail.is_active,
      where_clause: cfg.where_clause,
      incremental_column: cfg.incremental_column,
      incremental_start: cfg.incremental_start,
      cdc_poll_interval_sec: cfg.cdc?.poll_interval_sec ?? 10,
      batch_size: cfg.batch_size ?? 2000,
      pre_sql: cfg.pre_sql,
      post_sql: cfg.post_sql,
    })
    await loadTables(detail.src_datasource_id, 'src')
    await loadTables(detail.dst_datasource_id, 'dst')
    await loadSrcColumns(detail.src_datasource_id, detail.src_table)
    setDrawerOpen(true)
  }

  const buildPayload = (values: any) => {
    const sync_config: Record<string, unknown> = {
      batch_size: values.batch_size ?? 2000,
    }
    if (values.where_clause) sync_config.where_clause = values.where_clause
    if (values.pre_sql) sync_config.pre_sql = values.pre_sql
    if (values.post_sql) sync_config.post_sql = values.post_sql
    if (values.sync_mode === 'incremental' || values.sync_mode === 'cdc') {
      sync_config.incremental_column = values.incremental_column
      if (values.incremental_start) sync_config.incremental_start = values.incremental_start
    }
    if (values.sync_mode === 'cdc') {
      sync_config.cdc = {
        poll_interval_sec: values.cdc_poll_interval_sec ?? 10,
        running: false,
      }
    }
    if (mappings.length) sync_config.field_mappings = mappings.filter(m => m.src && m.dst)
    return {
      ...values,
      workspace_id: wsId,
      is_active: values.is_active !== false,
      schedule_cron: values.schedule_cron || null,
      sync_config,
    }
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    const payload = buildPayload(values)
    if (editingId) {
      await integrationApi.updateTask(editingId, payload)
      message.success('已保存')
    } else {
      await integrationApi.createTask(payload)
      message.success('已创建')
    }
    setDrawerOpen(false)
    load()
  }

  const handleValidate = async () => {
    if (!editingId) {
      message.info('请先保存任务后再校验')
      return
    }
    const res: any = await integrationApi.validateTask(editingId)
    const lines = [
      ...(res.warnings || []),
      `源库: ${res.src_connection?.ok ? '通' : '失败'} — ${res.src_connection?.message}`,
      `目标库: ${res.dst_connection?.ok ? '通' : '失败'} — ${res.dst_connection?.message}`,
    ]
    Modal.info({ title: '配置校验', content: <pre style={{ whiteSpace: 'pre-wrap' }}>{lines.join('\n')}</pre> })
  }

  const handleRun = async (id: number) => {
    const res: any = await integrationApi.runTask(id)
    message.success(res?.message || '已提交执行')
    load()
    const task = tasks.find(t => t.id === id)
    if (task) openHistory(task)
  }

  const openHistory = async (task: any) => {
    setHistoryTask(task)
    setHistoryOpen(true)
    const refresh = async () => {
      const list: any = await integrationApi.records(task.id, 50)
      setRecords(Array.isArray(list) ? list : [])
      const running = (list || []).some((r: any) => r.status === 'running')
      if (running) {
        pollRef.current = window.setTimeout(refresh, 2000)
      } else {
        load()
      }
    }
    await refresh()
  }

  useEffect(() => () => {
    if (pollRef.current) window.clearTimeout(pollRef.current)
  }, [])

  useEffect(() => {
    if (!historyOpen && pollRef.current) {
      window.clearTimeout(pollRef.current)
      pollRef.current = null
    }
  }, [historyOpen])

  const autoMapColumns = () => {
    const dstTable = form.getFieldValue('dst_table')
    const dstDs = form.getFieldValue('dst_datasource_id')
    if (!dstDs || !dstTable) {
      message.warning('请先选择目标数据源与目标表')
      return
    }
    integrationApi.getColumns(dstDs, dstTable).then((res: any) => {
      const dstNames = new Set((res?.columns || []).map((c: any) => c.name))
      const next = srcCols
        .filter((c: any) => dstNames.has(c.name))
        .map((c: any) => ({ src: c.name, dst: c.name }))
      setMappings(next)
      message.success(`已按同名列映射 ${next.length} 个字段`)
    })
  }

  const columns = [
    { title: '任务名称', dataIndex: 'name', ellipsis: true },
    {
      title: '源 → 目标',
      render: (_: unknown, row: any) => (
        <span style={{ fontSize: 12 }}>
          {row.src_table} → {row.dst_table}
        </span>
      ),
    },
    {
      title: '模式',
      dataIndex: 'sync_mode',
      width: 88,
      render: (t: string) => (
        <Tag color={t === 'cdc' ? 'purple' : 'blue'}>
          {t === 'incremental' ? '增量' : t === 'cdc' ? 'CDC' : '全量'}
        </Tag>
      ),
    },
    {
      title: '调度',
      dataIndex: 'schedule_cron',
      width: 120,
      ellipsis: true,
      render: (c: string) => c || <span style={{ color: '#bbb' }}>手动</span>,
    },
    {
      title: '状态',
      width: 100,
      render: (_: unknown, row: any) => (
        <Space size={4}>
          <Tag color={row.is_active ? 'green' : 'default'}>{row.is_active ? '启用' : '停用'}</Tag>
          {row.last_run_status && (
            <Tag color={STATUS_COLOR[row.last_run_status] || 'default'}>{row.last_run_status}</Tag>
          )}
        </Space>
      ),
    },
    { title: '最后同步', dataIndex: 'last_sync_at', width: 168, ellipsis: true },
    {
      title: '操作',
      width: 280,
      fixed: 'right' as const,
      render: (_: unknown, row: any) => (
        <Space size={0} wrap>
          {canRun && row.sync_mode !== 'cdc' && (
            <Button
              size="small"
              type="link"
              icon={<PlayCircleOutlined />}
              disabled={!row.is_active}
              onClick={() => handleRun(row.id)}
            >
              运行
            </Button>
          )}
          {canRun && row.sync_mode === 'cdc' && (
            <Button size="small" type="link" onClick={() => handleRun(row.id)}>
              执行一次
            </Button>
          )}
          {canRun && row.sync_mode === 'cdc' && (
            <>
              {!cdcStatusMap[row.id]?.running ? (
                <Button
                  size="small"
                  type="link"
                  onClick={async () => {
                    await integrationApi.cdcStart(row.id)
                    message.success('CDC 已启动')
                    load()
                  }}
                >
                  启动 CDC
                </Button>
              ) : (
                <Button
                  size="small"
                  type="link"
                  danger
                  onClick={async () => {
                    await integrationApi.cdcStop(row.id)
                    message.success('CDC 已停止')
                    load()
                  }}
                >
                  停止 CDC
                </Button>
              )}
            </>
          )}
          <Button size="small" type="link" icon={<HistoryOutlined />} onClick={() => openHistory(row)}>
            历史
          </Button>
          {canWrite && (
            <>
              <Button size="small" type="link" icon={<EditOutlined />} onClick={() => openEdit(row)}>
                编辑
              </Button>
              <Button
                size="small"
                type="link"
                onClick={async () => {
                  await integrationApi.toggleActive(row.id)
                  load()
                }}
              >
                {row.is_active ? '停用' : '启用'}
              </Button>
              <Popconfirm title="确认删除该任务？" onConfirm={() => integrationApi.deleteTask(row.id).then(load)}>
                <Button size="small" type="link" danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ]

  if (!wsId) {
    return <Alert type="warning" showIcon message="请先选择工作区" />
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0 }}>数据集成</h2>
          <p style={{ margin: '6px 0 0', color: '#666', fontSize: 13 }}>
            表级同步（PostgreSQL / Doris 等）。支持字段映射、增量/CDC 轮询准实时、Cron 调度、工作流 SYNC 节点与运行历史。
          </p>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          {canWrite && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建同步任务
            </Button>
          )}
        </Space>
      </div>

      {integrationDs.length === 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="当前工作空间暂无 mysql / postgresql / doris 类型数据源，请先在「数据源」中创建。"
        />
      )}

      <Table
        dataSource={tasks}
        columns={columns}
        rowKey="id"
        size="small"
        scroll={{ x: 1100 }}
        pagination={{ pageSize: 20, showSizeChanger: true }}
      />

      <Drawer
        title={editingId ? '编辑同步任务' : '新建同步任务'}
        width={720}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
        extra={
          <Space>
            {editingId && (
              <Button icon={<CheckCircleOutlined />} onClick={handleValidate}>
                校验连接
              </Button>
            )}
            {canWrite && (
              <Button type="primary" onClick={handleSave}>
                保存
              </Button>
            )}
          </Space>
        }
      >
        <Form form={form} layout="vertical" disabled={!canWrite}>
          <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
            <Input placeholder="如：订单表同步到数仓" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Tabs
            items={[
              {
                key: 'source',
                label: '数据来源',
                children: (
                  <>
                    <Form.Item name="src_datasource_id" label="源数据源" rules={[{ required: true }]}>
                      <Select
                        options={dsOptions}
                        onChange={v => {
                          loadTables(v, 'src')
                          form.setFieldValue('src_table', undefined)
                          setMappings([])
                        }}
                      />
                    </Form.Item>
                    <Form.Item name="src_table" label="源表" rules={[{ required: true }]}>
                      <Select
                        showSearch
                        options={srcTables}
                        onChange={t => loadSrcColumns(form.getFieldValue('src_datasource_id'), t)}
                      />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'target',
                label: '数据去向',
                children: (
                  <>
                    <Form.Item name="dst_datasource_id" label="目标数据源" rules={[{ required: true }]}>
                      <Select
                        options={dsOptions}
                        onChange={v => {
                          loadTables(v, 'dst')
                          form.setFieldValue('dst_table', undefined)
                        }}
                      />
                    </Form.Item>
                    <Form.Item name="dst_table" label="目标表" rules={[{ required: true }]}>
                      <Select showSearch options={dstTables} />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'mapping',
                label: '字段映射',
                children: (
                  <>
                    <Space style={{ marginBottom: 8 }}>
                      <Button size="small" onClick={autoMapColumns} disabled={!srcCols.length}>
                        按同名列自动映射
                      </Button>
                      <Button
                        size="small"
                        onClick={() => setMappings([...mappings, { src: '', dst: '' }])}
                      >
                        添加一行
                      </Button>
                      <span style={{ color: '#999', fontSize: 12 }}>留空则按源表列名 1:1 写入</span>
                    </Space>
                    <Table
                      size="small"
                      pagination={false}
                      rowKey={(_, i) => String(i)}
                      dataSource={mappings}
                      columns={[
                        {
                          title: '源字段',
                          render: (_: unknown, __: FieldMapping, idx: number) => (
                            <Select
                              style={{ width: '100%' }}
                              value={mappings[idx]?.src}
                              options={srcCols.map((c: any) => ({ label: c.name, value: c.name }))}
                              onChange={v => {
                                const next = [...mappings]
                                next[idx] = { ...next[idx], src: v }
                                setMappings(next)
                              }}
                            />
                          ),
                        },
                        {
                          title: '目标字段',
                          render: (_: unknown, row: FieldMapping, idx: number) => (
                            <Input
                              value={row.dst}
                              onChange={e => {
                                const next = [...mappings]
                                next[idx] = { ...next[idx], dst: e.target.value }
                                setMappings(next)
                              }}
                            />
                          ),
                        },
                        {
                          title: '',
                          width: 48,
                          render: (_: unknown, __: FieldMapping, idx: number) => (
                            <Button
                              type="link"
                              danger
                              size="small"
                              onClick={() => setMappings(mappings.filter((_, i) => i !== idx))}
                            >
                              删
                            </Button>
                          ),
                        },
                      ]}
                    />
                  </>
                ),
              },
              {
                key: 'sync',
                label: '同步策略',
                children: (
                  <>
                    <Form.Item name="sync_mode" label="同步模式" rules={[{ required: true }]}>
                      <Select options={SYNC_MODES} />
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate={(p, c) => p.sync_mode !== c.sync_mode}>
                      {() => {
                        const mode = form.getFieldValue('sync_mode')
                        if (mode !== 'incremental' && mode !== 'cdc') return null
                        return (
                          <>
                            <Form.Item name="incremental_column" label="增量 / CDC 水位字段" rules={[{ required: true }]}>
                              <Select
                                options={srcCols.map((c: any) => ({ label: c.name, value: c.name }))}
                                placeholder="如 updated_at / id"
                              />
                            </Form.Item>
                            <Form.Item name="incremental_start" label="起始水位">
                              <Input placeholder="1970-01-01 00:00:00" />
                            </Form.Item>
                            {mode === 'cdc' && (
                              <Form.Item name="cdc_poll_interval_sec" label="轮询间隔（秒）" initialValue={10}>
                                <InputNumber min={3} max={3600} style={{ width: '100%' }} />
                              </Form.Item>
                            )}
                          </>
                        )
                      }}
                    </Form.Item>
                    <Form.Item name="where_clause" label="源端过滤 WHERE（不含 WHERE 关键字）">
                      <Input placeholder="status = 1 AND dt >= '2026-01-01'" />
                    </Form.Item>
                    <Form.Item name="batch_size" label="批大小">
                      <InputNumber min={100} max={10000} style={{ width: '100%' }} />
                    </Form.Item>
                    <Form.Item name="pre_sql" label="目标端前置 SQL">
                      <Input.TextArea rows={2} placeholder="执行同步前在目标库执行（可选）" />
                    </Form.Item>
                    <Form.Item name="post_sql" label="目标端后置 SQL">
                      <Input.TextArea rows={2} placeholder="同步完成后执行（可选）" />
                    </Form.Item>
                  </>
                ),
              },
              {
                key: 'schedule',
                label: '调度',
                children: (
                  <>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="填写 Cron 后由平台调度器按点执行；留空则仅支持手动运行。"
                    />
                    <Form.Item name="schedule_cron" label="Cron 表达式">
                      <CronBuilder
                        value={form.getFieldValue('schedule_cron') || ''}
                        onChange={c => form.setFieldValue('schedule_cron', c)}
                      />
                    </Form.Item>
                  </>
                ),
              },
            ]}
          />
        </Form>
      </Drawer>

      <Drawer
        title={historyTask ? `运行历史 — ${historyTask.name}` : '运行历史'}
        width={640}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        <Table
          size="small"
          rowKey="id"
          dataSource={records}
          pagination={false}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 56 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 88,
              render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag>,
            },
            { title: '触发', dataIndex: 'trigger_type', width: 80 },
            {
              title: '读取/写入',
              render: (_: unknown, r: any) => `${r.rows_read ?? 0} / ${r.rows_written ?? 0}`,
            },
            {
              title: '耗时',
              dataIndex: 'duration_ms',
              width: 72,
              render: (ms: number) => (ms != null ? `${(ms / 1000).toFixed(1)}s` : '—'),
            },
            { title: '开始', dataIndex: 'started_at', ellipsis: true },
            {
              title: '错误',
              dataIndex: 'error_msg',
              ellipsis: true,
              render: (t: string) => (t ? <Tooltip title={t}><span style={{ color: '#cf1322' }}>{t.slice(0, 40)}…</span></Tooltip> : '—'),
            },
          ]}
        />
      </Drawer>
    </div>
  )
}

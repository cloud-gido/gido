/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useEffect, useMemo, useState, type Key } from 'react'
import {
  Alert, Button, Divider, Drawer, Form, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Switch, Table, Tag, Typography, Upload, message,
} from 'antd'
import {
  CloudDownloadOutlined, CloudUploadOutlined, CodeOutlined, CopyOutlined,
  PlayCircleOutlined, PlusOutlined, StopOutlined, UploadOutlined,
} from '@ant-design/icons'
import { dataServiceApi, approvalApi } from '../../api'
import { useAppStore } from '../../store'
import { can, isWorkspaceAdmin, P } from '../../perm'
import PublishApprovalModal from '../../components/PublishApprovalModal'
import { approvalPendingKey } from '../../approvalLabels'
import { useServiceData, useWorkspaceId } from './ServiceContext'
import { STATUS_COLOR, formatApiError } from './shared'
import ApiWizardBuilder, { type WizardConfig, type WizardParam } from './ApiWizardBuilder'

const { TextArea } = Input
const { Text, Paragraph } = Typography

function parseWizardConfig(raw: any): WizardConfig | null {
  if (!raw) return null
  let o: any = raw
  if (typeof raw === 'string') {
    try {
      o = JSON.parse(raw)
    } catch {
      return null
    }
  }
  if (typeof o !== 'object') return null
  const fieldsRaw = Array.isArray(o.fields) ? o.fields : []
  const fields = fieldsRaw.filter((f: string) => f && f !== '*')
  return {
    table: o.table || '',
    fields,
    filters: Array.isArray(o.filters) ? o.filters : [],
    order_by: Array.isArray(o.order_by)
      ? o.order_by
          .map((item: any) => {
            if (typeof item === 'string') return { column: item, direction: 'ASC' as const }
            const column = String(item?.column || '').trim()
            if (!column) return null
            const direction = String(item?.direction || 'ASC').toUpperCase() === 'DESC' ? 'DESC' as const : 'ASC' as const
            return { column, direction }
          })
          .filter(Boolean) as WizardConfig['order_by']
      : [],
  }
}

export default function ServiceApisPage() {
  const wsId = useWorkspaceId()
  const { user, currentWorkspace } = useAppStore()
  const { apis, datasources, loading, reload } = useServiceData()
  const canWrite = can(user, P.GIDO_SERVICE_WRITE, currentWorkspace)
  const canRun = can(user, P.GIDO_SERVICE_RUN, currentWorkspace)
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)

  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set())
  const [approvalTarget, setApprovalTarget] = useState<{ row: any; action: 'publish_api' | 'offline_api' } | null>(null)
  const [approvalNote, setApprovalNote] = useState('')

  const loadPending = async () => {
    if (!wsId) return
    try {
      const res: any = await approvalApi.list(wsId, { status: 'pending', page_size: 200 })
      setPendingKeys(
        new Set((res?.items || []).map((i: any) => approvalPendingKey(i.resource_type, i.resource_id, i.action))),
      )
    } catch {
      setPendingKeys(new Set())
    }
  }

  useEffect(() => {
    loadPending()
  }, [wsId])

  const refreshAll = async () => {
    await reload()
    await loadPending()
  }

  const [apiModal, setApiModal] = useState(false)
  const [editingApi, setEditingApi] = useState<any>(null)
  const [apiForm] = Form.useForm()
  const modeWatch = Form.useWatch('mode', apiForm)
  const datasourceWatch = Form.useWatch('datasource_id', apiForm)
  const [wizardConfig, setWizardConfig] = useState<WizardConfig>({ table: '', fields: [], filters: [], order_by: [] })
  const [testDrawer, setTestDrawer] = useState(false)
  const [testTarget, setTestTarget] = useState<any>(null)
  const [testParams, setTestParams] = useState('{}')
  const [testResult, setTestResult] = useState<any>(null)
  const [testError, setTestError] = useState<string | null>(null)
  const [docDrawer, setDocDrawer] = useState(false)
  const [docOpenApi, setDocOpenApi] = useState<any>(null)
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([])
  const [importOpen, setImportOpen] = useState(false)
  const [importBundle, setImportBundle] = useState<any>(null)
  const [importing, setImporting] = useState(false)

  const copyText = (t: string) => {
    navigator.clipboard.writeText(t).then(() => message.success('已复制'))
  }

  const downloadJson = (data: any, filename: string) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleExportSelected = async () => {
    if (!wsId) return
    const ids = selectedRowKeys.map(Number).filter(Boolean)
    if (!ids.length) {
      message.warning('请先勾选要导出的 API')
      return
    }
    try {
      const bundle: any = await dataServiceApi.exportApisBundle({ workspace_id: wsId, api_ids: ids })
      downloadJson(bundle, `gido-serve-apis-${wsId}-${ids.length}.json`)
      message.success(`已导出 ${bundle?.apis?.length || ids.length} 条 API 配置`)
    } catch (e: any) {
      message.error(formatApiError(e, '导出失败'))
    }
  }

  const handleExportOne = async (row: any) => {
    try {
      const bundle: any = await dataServiceApi.exportApiBundle(row.id)
      downloadJson(bundle, `gido-serve-api-${row.api_code || row.id}.json`)
      message.success('已下载配置')
    } catch (e: any) {
      message.error(formatApiError(e, '导出失败'))
    }
  }

  const openImport = () => {
    setImportBundle(null)
    setImportOpen(true)
  }

  const onImportFile = (file: File) => {
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result || ''))
        if (!parsed?.apis || !Array.isArray(parsed.apis)) {
          message.error('配置包须包含 apis 数组')
          return
        }
        setImportBundle(parsed)
        message.success(`已读取 ${parsed.apis.length} 条 API`)
      } catch {
        message.error('JSON 解析失败')
      }
    }
    reader.readAsText(file)
    return false
  }

  const submitImport = async () => {
    if (!wsId || !importBundle) {
      message.warning('请先选择配置文件')
      return
    }
    try {
      setImporting(true)
      const res: any = await dataServiceApi.importApisBundle({
        workspace_id: wsId,
        bundle: importBundle,
        on_conflict: 'overwrite',
      })
      message.success(res?.message || '导入完成')
      setImportOpen(false)
      setSelectedRowKeys([])
      await refreshAll()
    } catch (e: any) {
      message.error(formatApiError(e, '导入失败'))
    } finally {
      setImporting(false)
    }
  }

  const openCreateApi = () => {
    setEditingApi(null)
    apiForm.resetFields()
    setWizardConfig({ table: '', fields: [], filters: [], order_by: [] })
    apiForm.setFieldsValue({
      mode: 'wizard',
      http_method: 'GET',
      pagination_enabled: true,
      page_size_default: 20,
      page_size_max: 1000,
      timeout_seconds: 30,
      cache_ttl_seconds: 0,
      max_rows: 10000,
      params: [],
      sql_template: '',
    })
    setApiModal(true)
  }

  const openEditApi = (row: any) => {
    setEditingApi(row)
    const wizard = parseWizardConfig(row.wizard_config) || { table: '', fields: [], filters: [], order_by: [] }
    setWizardConfig(wizard)
    apiForm.setFieldsValue({
      ...row,
      params: row.params || [],
    })
    setApiModal(true)
  }

  const handleWizardChange = (cfg: WizardConfig, params: WizardParam[], sqlPreview: string) => {
    setWizardConfig(cfg)
    apiForm.setFieldsValue({
      params,
      sql_template: sqlPreview.startsWith('--') ? apiForm.getFieldValue('sql_template') : sqlPreview,
    })
  }

  const handleUpgradeToSql = (sql: string, params: WizardParam[]) => {
    apiForm.setFieldsValue({
      mode: 'sql',
      sql_template: sql,
      params,
    })
    message.success('已切换为 SQL 模式，可继续编辑模板')
  }

  const saveApi = async () => {
    try {
      const v = await apiForm.validateFields()
      if (!wsId) return
      if (v.mode === 'wizard' && !wizardConfig.table) {
        message.error('请选择数据表')
        return
      }
      const payload: any = {
        name: v.name,
        description: v.description,
        mode: v.mode,
        http_method: v.http_method,
        datasource_id: v.datasource_id,
        sql_template: v.sql_template,
        pagination_enabled: v.pagination_enabled,
        page_size_default: v.page_size_default,
        page_size_max: v.page_size_max,
        timeout_seconds: v.timeout_seconds,
        cache_ttl_seconds: v.cache_ttl_seconds,
        max_rows: v.max_rows,
        params: (v.params || []).map((p: any, i: number) => ({
          name: p.name,
          param_in: p.param_in || 'query',
          data_type: p.data_type || 'string',
          required: !!p.required,
          default_value: p.default_value,
          description: p.description,
          validator_regex: p.validator_regex,
          sort_order: p.sort_order ?? i,
        })),
      }
      if (v.mode === 'wizard') {
        payload.wizard_config = {
          table: wizardConfig.table,
          fields: wizardConfig.fields?.length ? wizardConfig.fields : ['*'],
          filters: (wizardConfig.filters || []).map(f => ({
            column: f.column,
            op: f.op || '=',
            param: f.param,
          })),
          order_by: (wizardConfig.order_by || [])
            .filter(o => o.column)
            .map(o => ({
              column: o.column,
              direction: (o.direction || 'ASC').toUpperCase() === 'DESC' ? 'DESC' : 'ASC',
            })),
        }
      } else {
        payload.wizard_config = null
      }
      if (editingApi) {
        await dataServiceApi.updateApi(editingApi.id, payload)
        message.success('已保存')
      } else {
        await dataServiceApi.createApi({ ...payload, workspace_id: wsId, api_code: v.api_code })
        message.success('已创建')
      }
      setApiModal(false)
      refreshAll()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(formatApiError(e, '保存失败'))
    }
  }

  const openTest = async (row: any) => {
    try {
      const fresh: any = await dataServiceApi.getApi(row.id)
      setTestTarget(fresh)
      const defaults: Record<string, string> = {}
      for (const p of fresh.params || []) {
        if (p.default_value) defaults[p.name] = p.default_value
      }
      setTestParams(JSON.stringify(defaults, null, 2))
      setTestResult(null)
      setTestError(null)
      setTestDrawer(true)
    } catch (e: any) {
      message.error(formatApiError(e, '加载 API 失败'))
    }
  }

  const runTest = async () => {
    if (!testTarget) return
    let params = {}
    try {
      params = JSON.parse(testParams || '{}')
    } catch {
      message.error('参数须为合法 JSON，例如 {"fixture_id": "FX001"}')
      return
    }
    try {
      setTestError(null)
      const res = await dataServiceApi.testApi(testTarget.id, { params })
      setTestResult(res)
    } catch (e: any) {
      const detail = formatApiError(e, '测试失败')
      setTestError(detail)
      message.error(detail)
    }
  }

  const handlePublish = async (row: any) => {
    if (canPublishDirect) {
      await dataServiceApi.publishApi(row.id)
      message.success('已发布上线')
      await refreshAll()
      return
    }
    setApprovalNote('')
    setApprovalTarget({ row, action: 'publish_api' })
  }

  const handleOffline = async (row: any) => {
    if (canPublishDirect) {
      await dataServiceApi.offlineApi(row.id)
      message.success('已下线')
      await refreshAll()
      return
    }
    setApprovalNote('')
    setApprovalTarget({ row, action: 'offline_api' })
  }

  const submitPublishApproval = async () => {
    if (!approvalTarget || !wsId) return
    try {
      await approvalApi.submit({
        workspace_id: wsId,
        resource_type: 'data_service_api',
        resource_id: approvalTarget.row.id,
        action: approvalTarget.action,
        submit_note: approvalNote || undefined,
      })
      message.success('已提交审批')
      setApprovalTarget(null)
      setApprovalNote('')
      await refreshAll()
    } catch (e: any) {
      message.error(formatApiError(e, '提交失败'))
    }
  }

  const isApiPending = (row: any, action: 'publish_api' | 'offline_api') =>
    pendingKeys.has(approvalPendingKey('data_service_api', row.id, action))

  const apiColumns = useMemo(() => [
    { title: '名称', dataIndex: 'name', width: 140, ellipsis: true },
    { title: 'API Code', dataIndex: 'api_code', width: 120 },
    {
      title: '模式', dataIndex: 'mode', width: 88,
      render: (m: string) => <Tag color={m === 'wizard' ? 'blue' : 'default'}>{m === 'wizard' ? '可视化' : m === 'sql' ? 'SQL' : m}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', width: 140,
      render: (s: string, row: any) => (
        <Space size={4} wrap>
          <Tag color={STATUS_COLOR[s] || 'default'}>{s === 'online' ? '已上线' : s === 'offline' ? '已下线' : '草稿'}</Tag>
          {row.has_pending_publish && <Tag color="orange">待发布变更</Tag>}
        </Space>
      ),
    },
    { title: '版本', dataIndex: 'version', width: 60 },
    { title: '数据源', dataIndex: 'datasource_name', width: 100, ellipsis: true, render: (v: string) => v || '—' },
    {
      title: '开放路径', dataIndex: 'open_path', ellipsis: true,
      render: (p: string) => (
        <Space size={4}>
          <Text code style={{ fontSize: 11 }}>{p}</Text>
          <Button type="link" size="small" icon={<CopyOutlined />} onClick={() => copyText(p)} />
        </Space>
      ),
    },
    {
      title: '操作', width: 360, render: (_: any, row: any) => (
        <Space wrap size={4}>
          {canWrite && <Button size="small" onClick={() => openEditApi(row)}>编辑</Button>}
          {canRun && (
            <>
              <Button size="small" icon={<PlayCircleOutlined />} onClick={() => openTest(row)}>测试</Button>
              {(row.status !== 'online' || row.has_pending_publish) && (
                <Button
                  size="small"
                  type="primary"
                  icon={<CloudUploadOutlined />}
                  disabled={isApiPending(row, 'publish_api')}
                  onClick={() => handlePublish(row)}
                >
                  {isApiPending(row, 'publish_api')
                    ? '审批中'
                    : canPublishDirect
                      ? (row.has_pending_publish ? '发布变更' : '发布')
                      : '提交审批'}
                </Button>
              )}
              {row.has_pending_publish && canWrite && (
                <Popconfirm title="丢弃待发布配置？线上定义保持不变" onConfirm={async () => {
                  try {
                    await dataServiceApi.discardPendingApi(row.id)
                    message.success('已丢弃待发布配置')
                    refreshAll()
                  } catch (e: any) {
                    message.error(formatApiError(e, '操作失败'))
                  }
                }}>
                  <Button size="small">丢弃待发</Button>
                </Popconfirm>
              )}
              {row.status === 'online' && (
                <Button
                  size="small"
                  icon={<StopOutlined />}
                  disabled={isApiPending(row, 'offline_api')}
                  onClick={() => handleOffline(row)}
                >
                  {isApiPending(row, 'offline_api') ? '审批中' : canPublishDirect ? '下线' : '提交下线审批'}
                </Button>
              )}
            </>
          )}
          <Button size="small" icon={<CloudDownloadOutlined />} onClick={() => handleExportOne(row)}>下载配置</Button>
          <Button size="small" icon={<CodeOutlined />} onClick={async () => {
            setDocOpenApi(await dataServiceApi.openapi(row.id))
            setDocDrawer(true)
          }}>文档</Button>
          {canWrite && (
            <Popconfirm title="删除 API？关联授权与调用日志将一并清理" onConfirm={async () => {
              try {
                await dataServiceApi.deleteApi(row.id)
                message.success('已删除')
                refreshAll()
              } catch (e: any) {
                message.error(formatApiError(e, '删除失败'))
              }
            }}>
              <Button size="small" danger>删除</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ], [canWrite, canRun, canPublishDirect, pendingKeys, reload])

  if (!wsId) return <Alert type="info" message="请先选择工作空间" showIcon />

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>API 开发</h2>
          <Text type="secondary">可视化选表生成 API，或手写 SQL 模板；发布后可通过开放网关对外调用</Text>
        </div>
        <Space>
          <Button
            icon={<CloudDownloadOutlined />}
            disabled={!selectedRowKeys.length}
            onClick={handleExportSelected}
          >
            导出选中{selectedRowKeys.length ? ` (${selectedRowKeys.length})` : ''}
          </Button>
          {canWrite && (
            <Button icon={<UploadOutlined />} onClick={openImport}>导入配置</Button>
          )}
          {canWrite && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreateApi}>新建 API</Button>
          )}
        </Space>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="跨环境迁移：测试导出 → 生产导入。已上线接口导入后不停服，挂「待发布变更」，发布时才切换；新建/草稿则直接落草稿。"
      />

      <Table
        dataSource={apis}
        columns={apiColumns}
        rowKey="id"
        loading={loading}
        scroll={{ x: 1180 }}
        size="middle"
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
        }}
      />

      <Modal
        title={editingApi ? `编辑 API - ${editingApi.name}` : '新建 API'}
        open={apiModal}
        onOk={saveApi}
        onCancel={() => setApiModal(false)}
        width={modeWatch === 'wizard' ? 980 : 820}
        okText="保存"
        destroyOnClose
      >
        <Form form={apiForm} layout="vertical">
          {!editingApi && (
            <Form.Item name="api_code" label="API Code（小写+下划线，发布后路径的一部分）" rules={[{ required: true }]}>
              <Input placeholder="例如 get_all_order" />
            </Form.Item>
          )}
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><TextArea rows={2} /></Form.Item>
          <Space wrap style={{ width: '100%' }}>
            <Form.Item name="mode" label="创建模式" style={{ width: 180 }}>
              <Select options={[
                { value: 'wizard', label: '可视化选表' },
                { value: 'sql', label: 'SQL 脚本' },
              ]} />
            </Form.Item>
            <Form.Item name="http_method" label="HTTP 方法" style={{ width: 120 }}>
              <Select options={[{ value: 'GET' }, { value: 'POST' }]} />
            </Form.Item>
            <Form.Item name="datasource_id" label="数据源" rules={[{ required: true }]}>
              <Select
                style={{ width: 220 }}
                options={datasources.map(d => ({ value: d.id, label: d.name }))}
                placeholder="选择数据源"
                onChange={() => {
                  if (apiForm.getFieldValue('mode') === 'wizard') {
                    setWizardConfig({ table: '', fields: [], filters: [], order_by: [] })
                    apiForm.setFieldsValue({ params: [] })
                  }
                }}
              />
            </Form.Item>
          </Space>

          {modeWatch === 'wizard' ? (
            <>
              <Divider orientation="left" plain>可视化配置</Divider>
              <ApiWizardBuilder
                datasourceId={datasourceWatch}
                value={wizardConfig}
                onChange={handleWizardChange}
                onUpgradeToSql={handleUpgradeToSql}
              />
            </>
          ) : (
            <Form.Item name="sql_template" label="SQL 模板（参数用 :param_name）" rules={[{ required: true }]}>
              <TextArea rows={6} placeholder="SELECT * FROM db.table WHERE id = :id" />
            </Form.Item>
          )}

          <Divider orientation="left" plain>请求参数{modeWatch === 'wizard' ? '（由过滤条件自动生成，可微调）' : ''}</Divider>
          <Form.List name="params">
            {(fields, { add, remove }) => (
              <>
                {fields.map(({ key, name, ...rest }) => (
                  <Space key={key} align="baseline" wrap>
                    <Form.Item {...rest} name={[name, 'name']} rules={[{ required: true }]}>
                      <Input placeholder="参数名" style={{ width: 110 }} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'data_type']} initialValue="string">
                      <Select style={{ width: 100 }} options={[
                        { value: 'string', label: 'string' }, { value: 'int', label: 'int' },
                        { value: 'long', label: 'long' }, { value: 'float', label: 'float' },
                        { value: 'bool', label: 'bool' }, { value: 'date', label: 'date' },
                      ]} />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'required']} valuePropName="checked">
                      <Switch checkedChildren="必填" unCheckedChildren="可选" />
                    </Form.Item>
                    <Form.Item {...rest} name={[name, 'default_value']}>
                      <Input placeholder="默认值" style={{ width: 100 }} />
                    </Form.Item>
                    <Button type="link" danger onClick={() => remove(name)}>删</Button>
                  </Space>
                ))}
                {modeWatch !== 'wizard' && (
                  <Button type="dashed" onClick={() => add()} block>+ 添加参数</Button>
                )}
              </>
            )}
          </Form.List>
          <Divider orientation="left" plain>运行策略</Divider>
          <Space wrap>
            <Form.Item name="pagination_enabled" label="分页" valuePropName="checked"><Switch /></Form.Item>
            <Form.Item name="page_size_default" label="默认页大小"><InputNumber min={1} max={1000} /></Form.Item>
            <Form.Item name="timeout_seconds" label="超时(秒)"><InputNumber min={3} max={120} /></Form.Item>
            <Form.Item name="cache_ttl_seconds" label="缓存 TTL(秒，0=关)"><InputNumber min={0} max={3600} /></Form.Item>
          </Space>
        </Form>
      </Modal>

      <Drawer title={`测试 API - ${testTarget?.name}`} open={testDrawer} onClose={() => setTestDrawer(false)} width={560}>
        <Paragraph type="secondary">JSON 参数，键名须与 SQL 中 `:param_name` 一致</Paragraph>
        <TextArea rows={6} value={testParams} onChange={e => setTestParams(e.target.value)} />
        <Button type="primary" icon={<PlayCircleOutlined />} onClick={runTest} style={{ marginTop: 12 }}>执行测试</Button>
        {testError && <Alert type="error" showIcon message={testError} style={{ marginTop: 16 }} />}
        {testResult && (
          <div style={{ marginTop: 16 }}>
            <Text>
              TraceId: {testResult.trace_id} · {testResult.latency_ms}ms
              {testResult.success != null ? ` · code=${testResult.code}` : ''}
            </Text>
            <pre style={{ marginTop: 8, background: '#f5f5f5', padding: 12, maxHeight: 360, overflow: 'auto', fontSize: 12 }}>
              {JSON.stringify(
                {
                  code: testResult.code,
                  success: testResult.success,
                  message: testResult.message,
                  data: testResult.data,
                },
                null,
                2,
              )}
            </pre>
          </div>
        )}
      </Drawer>

      <Drawer title="OpenAPI 文档" open={docDrawer} onClose={() => setDocDrawer(false)} width={520}>
        {docOpenApi && (
          <>
            <Button icon={<CopyOutlined />} onClick={() => copyText(JSON.stringify(docOpenApi, null, 2))}>复制 JSON</Button>
            <pre style={{ marginTop: 12, background: '#f5f5f5', padding: 12, maxHeight: '70vh', overflow: 'auto', fontSize: 11 }}>
              {JSON.stringify(docOpenApi, null, 2)}
            </pre>
          </>
        )}
      </Drawer>

      <PublishApprovalModal
        open={!!approvalTarget}
        title={
          approvalTarget?.action === 'offline_api'
            ? `提交下线审批 — ${approvalTarget?.row?.name || ''}`
            : `提交发布审批 — ${approvalTarget?.row?.name || ''}`
        }
        hint={
          approvalTarget?.action === 'offline_api'
            ? '普通开发不能直接下线生产 API。审批通过后系统将自动下线该 API。'
            : '普通开发不能直接发布 API 到生产网关。审批通过后将自动上线。'
        }
        note={approvalNote}
        onNoteChange={setApprovalNote}
        onCancel={() => { setApprovalTarget(null); setApprovalNote('') }}
        onSubmit={submitPublishApproval}
      />

      <Modal
        title="导入 API 配置包"
        open={importOpen}
        onOk={submitImport}
        onCancel={() => setImportOpen(false)}
        okText="一键导入"
        confirmLoading={importing}
        okButtonProps={{ disabled: !importBundle }}
        width={520}
        destroyOnClose
      >
        <Paragraph type="secondary" style={{ marginTop: 0 }}>
          选择测试环境导出的 JSON 即可。系统自动按 API Code 对齐；数据源优先同名，否则按类型自动匹配（如测试/生产各一个 Doris）。
          新建为草稿；已上线则挂待发布、不停服。
        </Paragraph>
        <Upload beforeUpload={onImportFile as any} maxCount={1} accept=".json,application/json">
          <Button type="primary" icon={<UploadOutlined />}>选择配置文件</Button>
        </Upload>
        {importBundle && (
          <Alert
            style={{ marginTop: 12 }}
            type="success"
            showIcon
            message={`已加载 ${importBundle.apis?.length || 0} 条 API，点「一键导入」即可`}
          />
        )}
      </Modal>
    </div>
  )
}

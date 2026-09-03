/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Button, Drawer, Form, Input, Select, Space, Switch, Table, Tabs, Upload,
  message, Typography,
} from 'antd'
import { InboxOutlined, CloudUploadOutlined } from '@ant-design/icons'
import { datasourceApi, integrationApi } from '../api'

type ColDef = {
  name: string
  type: string
  nullable: boolean
  is_primary_key: boolean
  source_header?: string
}

type Props = {
  open: boolean
  workspaceId: number
  canWrite: boolean
  canRun: boolean
  defaultDatasourceId?: number | null
  onClose: () => void
  onDone: () => void
}

const TYPE_OPTIONS = [
  { label: 'string', value: 'string' },
  { label: 'bigint', value: 'bigint' },
  { label: 'double', value: 'double' },
  { label: 'boolean', value: 'boolean' },
  { label: 'datetime', value: 'datetime' },
]

function suggestTableName(filename?: string) {
  const base = (filename || 'import').replace(/\.[^.]+$/, '')
  const cleaned = base.replace(/[^\w\u4e00-\u9fff]+/g, '_').replace(/^(\d)/, 't_$1').slice(0, 48)
  const d = new Date()
  const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`
  return `import_${cleaned || 'data'}_${stamp}`.slice(0, 64)
}

export default function FileImportDrawer({
  open, workspaceId, canWrite, canRun, defaultDatasourceId, onClose, onDone,
}: Props) {
  const [form] = Form.useForm()
  const [tab, setTab] = useState('file')
  const [uploading, setUploading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [datasources, setDatasources] = useState<any[]>([])
  const [fileMeta, setFileMeta] = useState<any>(null)
  const [columns, setColumns] = useState<ColDef[]>([])
  const [previewRows, setPreviewRows] = useState<any[][]>([])
  const [ddl, setDdl] = useState('')
  const [tableExists, setTableExists] = useState(false)

  const targetDs = useMemo(
    () => datasources.filter((d: any) => ['mysql', 'doris'].includes((d.ds_type || '').toLowerCase())),
    [datasources],
  )

  useEffect(() => {
    if (!open || !workspaceId) return
    datasourceApi.list(workspaceId).then((d: any) => setDatasources(Array.isArray(d) ? d : []))
    form.setFieldsValue({
      has_header: true,
      encoding: undefined,
      delimiter: undefined,
      register_datamap: true,
      if_exists: 'fail',
      run_now: true,
      dst_datasource_id: defaultDatasourceId || undefined,
    })
    setTab('file')
    setFileMeta(null)
    setColumns([])
    setPreviewRows([])
    setDdl('')
    setTableExists(false)
  }, [open, workspaceId, defaultDatasourceId])

  const applyParseResult = (res: any) => {
    setFileMeta(res)
    setColumns((res.columns || []).map((c: any) => ({
      name: c.name,
      type: c.type || 'string',
      nullable: c.nullable !== false,
      is_primary_key: !!c.is_primary_key,
      source_header: c.source_header,
    })))
    setPreviewRows(res.preview_rows || [])
    form.setFieldsValue({
      encoding: res.encoding && res.encoding !== 'binary' ? res.encoding : form.getFieldValue('encoding'),
      delimiter: res.delimiter ?? form.getFieldValue('delimiter'),
      has_header: res.has_header !== false,
      sheet_name: res.sheet_name,
      name: form.getFieldValue('name') || `导入 ${res.original_filename || ''}`.trim(),
      dst_table: form.getFieldValue('dst_table') || suggestTableName(res.original_filename),
    })
  }

  const handleUpload = async (file: File) => {
    if (!canWrite) return false
    setUploading(true)
    try {
      const res: any = await integrationApi.uploadFileImport(workspaceId, file, {
        has_header: form.getFieldValue('has_header') !== false,
        encoding: form.getFieldValue('encoding'),
        delimiter: form.getFieldValue('delimiter'),
        sheet_name: form.getFieldValue('sheet_name'),
      })
      applyParseResult(res)
      message.success(`已解析 ${res.row_count ?? 0} 行`)
      setTab('schema')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '上传失败')
    } finally {
      setUploading(false)
    }
    return false
  }

  const refreshPreview = async () => {
    if (!fileMeta?.file_id) {
      message.warning('请先上传文件')
      return
    }
    setUploading(true)
    try {
      const res: any = await integrationApi.previewFileImport({
        workspace_id: workspaceId,
        file_id: fileMeta.file_id,
        encoding: form.getFieldValue('encoding'),
        delimiter: form.getFieldValue('delimiter'),
        has_header: form.getFieldValue('has_header') !== false,
        sheet_name: form.getFieldValue('sheet_name'),
      })
      applyParseResult(res)
      message.success('已按当前选项重新解析')
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '解析失败')
    } finally {
      setUploading(false)
    }
  }

  const refreshDdl = async () => {
    const dsId = form.getFieldValue('dst_datasource_id')
    const table = form.getFieldValue('dst_table')
    if (!dsId || !table || !columns.length) {
      setDdl('')
      return
    }
    try {
      const res: any = await integrationApi.previewFileImportDdl({
        datasource_id: dsId,
        table_name: table,
        columns: columns.map(({ name, type, nullable, is_primary_key }) => ({
          name, type, nullable, is_primary_key,
        })),
      })
      setDdl(res.ddl || '')
      setTableExists(!!res.table_exists)
    } catch (e: any) {
      setDdl('')
      message.error(e?.response?.data?.detail || e?.message || '生成 DDL 失败')
    }
  }

  useEffect(() => {
    if (tab === 'confirm' || tab === 'target') {
      refreshDdl()
    }
  }, [tab])

  const handleSubmit = async () => {
    if (!canWrite) return
    try {
      const values = await form.validateFields([
        'name', 'dst_datasource_id', 'dst_table', 'if_exists', 'register_datamap', 'run_now',
      ])
      if (!fileMeta?.file_id) {
        message.warning('请先上传文件')
        setTab('file')
        return
      }
      if (!columns.length) {
        message.warning('请确认字段定义')
        setTab('schema')
        return
      }
      if (values.run_now && !canRun) {
        message.warning('无运行权限，请取消「立即导入」后仅创建任务')
        return
      }
      setSubmitting(true)
      const res: any = await integrationApi.createFileImportTask({
        workspace_id: workspaceId,
        name: values.name,
        description: values.description,
        dst_datasource_id: values.dst_datasource_id,
        dst_table: values.dst_table,
        file_id: fileMeta.file_id,
        columns: columns.map(({ name, type, nullable, is_primary_key }) => ({
          name, type, nullable, is_primary_key,
        })),
        encoding: form.getFieldValue('encoding'),
        delimiter: form.getFieldValue('delimiter'),
        has_header: form.getFieldValue('has_header') !== false,
        sheet_name: form.getFieldValue('sheet_name'),
        register_datamap: !!values.register_datamap,
        if_exists: values.if_exists || 'fail',
        run_now: !!values.run_now,
      })
      message.success(res?.message || '已创建')
      onDone()
      onClose()
    } catch (e: any) {
      if (e?.errorFields) return
      message.error(e?.response?.data?.detail || e?.message || '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Drawer
      title="本地文件导入"
      width={760}
      open={open}
      onClose={onClose}
      destroyOnClose
      extra={
        <Space>
          <Button onClick={onClose}>取消</Button>
          {canWrite && (
            <Button type="primary" loading={submitting} icon={<CloudUploadOutlined />} onClick={handleSubmit}>
              开始导入
            </Button>
          )}
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="上传 CSV（推荐大文件，最大约 3GB / 500 万行）或 Excel（≤200MB）。Doris 目标走 Stream Load；MySQL 走流式批量插入。导入在后台执行，可在运行历史查看进度。"
      />
      <Form form={form} layout="vertical" disabled={!canWrite}>
        <Tabs
          activeKey={tab}
          onChange={setTab}
          items={[
            {
              key: 'file',
              label: '选择文件',
              children: (
                <>
                  <Upload.Dragger
                    accept=".csv,.txt,.tsv,.xlsx,.xlsm"
                    multiple={false}
                    showUploadList={false}
                    beforeUpload={handleUpload}
                    disabled={!canWrite || uploading}
                  >
                    <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                    <p className="ant-upload-text">点击或拖拽文件到此处</p>
                    <p className="ant-upload-hint">
                      大文件请用 CSV（约 ≤3GB）；Excel 仅适合 ≤200MB。UTF-8 / GBK 均可。
                    </p>
                  </Upload.Dragger>
                  {fileMeta && (
                    <Alert
                      style={{ marginTop: 12 }}
                      type="success"
                      showIcon
                      message={
                        `${fileMeta.original_filename} · ${fileMeta.row_count ?? 0} 行`
                        + `${fileMeta.row_count_estimated ? '（估算）' : ''}`
                        + ` · ${((fileMeta.size_bytes || 0) / (1024 * 1024)).toFixed(1)} MB`
                      }
                    />
                  )}
                  <Form.Item name="has_header" label="首行为表头" valuePropName="checked" style={{ marginTop: 16 }}>
                    <Switch />
                  </Form.Item>
                  {fileMeta?.format === 'csv' && (
                    <>
                      <Form.Item name="encoding" label="编码">
                        <Select
                          allowClear
                          placeholder="自动探测"
                          options={[
                            { label: 'UTF-8', value: 'utf-8' },
                            { label: 'UTF-8 BOM', value: 'utf-8-sig' },
                            { label: 'GBK', value: 'gbk' },
                            { label: 'GB18030', value: 'gb18030' },
                          ]}
                        />
                      </Form.Item>
                      <Form.Item name="delimiter" label="分隔符">
                        <Select
                          allowClear
                          placeholder="自动探测"
                          options={[
                            { label: '逗号 ,', value: ',' },
                            { label: '制表符 Tab', value: '\t' },
                            { label: '分号 ;', value: ';' },
                            { label: '竖线 |', value: '|' },
                          ]}
                        />
                      </Form.Item>
                    </>
                  )}
                  {fileMeta?.format === 'xlsx' && (
                    <Form.Item name="sheet_name" label="工作表">
                      <Select
                        options={(fileMeta.sheet_names || []).map((s: string) => ({ label: s, value: s }))}
                      />
                    </Form.Item>
                  )}
                  {fileMeta && (
                    <Button loading={uploading} onClick={refreshPreview}>按当前选项重新解析</Button>
                  )}
                </>
              ),
            },
            {
              key: 'schema',
              label: '字段与建表',
              children: (
                <>
                  {!fileMeta ? (
                    <Alert type="warning" showIcon message="请先上传文件" />
                  ) : (
                    <>
                      <Table
                        size="small"
                        pagination={false}
                        rowKey={(_, i) => String(i)}
                        dataSource={columns}
                        scroll={{ x: 640 }}
                        columns={[
                          {
                            title: '列名',
                            width: 160,
                            render: (_: unknown, row: ColDef, idx: number) => (
                              <Input
                                value={row.name}
                                onChange={e => {
                                  const next = [...columns]
                                  next[idx] = { ...next[idx], name: e.target.value }
                                  setColumns(next)
                                }}
                              />
                            ),
                          },
                          {
                            title: '类型',
                            width: 120,
                            render: (_: unknown, row: ColDef, idx: number) => (
                              <Select
                                style={{ width: '100%' }}
                                value={row.type}
                                options={TYPE_OPTIONS}
                                onChange={v => {
                                  const next = [...columns]
                                  next[idx] = { ...next[idx], type: v }
                                  setColumns(next)
                                }}
                              />
                            ),
                          },
                          {
                            title: '主键',
                            width: 72,
                            render: (_: unknown, row: ColDef, idx: number) => (
                              <Switch
                                checked={row.is_primary_key}
                                onChange={v => {
                                  const next = [...columns]
                                  next[idx] = { ...next[idx], is_primary_key: v }
                                  setColumns(next)
                                }}
                              />
                            ),
                          },
                          {
                            title: '可空',
                            width: 72,
                            render: (_: unknown, row: ColDef, idx: number) => (
                              <Switch
                                checked={row.nullable}
                                onChange={v => {
                                  const next = [...columns]
                                  next[idx] = { ...next[idx], nullable: v }
                                  setColumns(next)
                                }}
                              />
                            ),
                          },
                          {
                            title: '源表头',
                            dataIndex: 'source_header',
                            ellipsis: true,
                            render: (v: string) => v || '—',
                          },
                        ]}
                      />
                      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 8 }}>
                        数据预览（前 {previewRows.length} 行）
                      </Typography.Paragraph>
                      <Table
                        size="small"
                        pagination={false}
                        scroll={{ x: Math.max(600, columns.length * 120) }}
                        rowKey={(_, i) => String(i)}
                        dataSource={previewRows}
                        columns={columns.map((c, i) => ({
                          title: c.name,
                          ellipsis: true,
                          width: 120,
                          render: (_: unknown, row: any[]) => {
                            const v = Array.isArray(row) ? row[i] : undefined
                            return v == null || v === '' ? <span style={{ color: '#bbb' }}>—</span> : String(v)
                          },
                        }))}
                      />
                    </>
                  )}
                </>
              ),
            },
            {
              key: 'target',
              label: '数据去向',
              children: (
                <>
                  <Form.Item name="name" label="任务名称" rules={[{ required: true }]}>
                    <Input placeholder="如：导入订单样例" />
                  </Form.Item>
                  <Form.Item name="description" label="描述">
                    <Input.TextArea rows={2} />
                  </Form.Item>
                  <Form.Item name="dst_datasource_id" label="目标数据源" rules={[{ required: true }]}>
                    <Select
                      options={targetDs.map((d: any) => ({
                        label: `${d.name} (${d.ds_type})`,
                        value: d.id,
                      }))}
                      onChange={() => refreshDdl()}
                      placeholder={targetDs.length ? '选择 MySQL / Doris' : '暂无可用数据源'}
                    />
                  </Form.Item>
                  <Form.Item
                    name="dst_table"
                    label="目标表"
                    rules={[
                      { required: true },
                      { pattern: /^[A-Za-z_][A-Za-z0-9_]*$/, message: '仅字母数字下划线，且不能以数字开头' },
                    ]}
                  >
                    <Input placeholder="新建物理表名" onBlur={() => refreshDdl()} />
                  </Form.Item>
                  <Form.Item name="if_exists" label="表已存在时">
                    <Select
                      options={[
                        { label: '失败（仅新建）', value: 'fail' },
                        { label: '追加写入', value: 'append' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="register_datamap" label="导入后注册到数据字典" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  {tableExists && form.getFieldValue('if_exists') === 'fail' && (
                    <Alert type="warning" showIcon message="目标表已存在，请更换表名或改为追加写入" />
                  )}
                </>
              ),
            },
            {
              key: 'confirm',
              label: '装载与确认',
              children: (
                <>
                  <Form.Item name="run_now" label="创建后立即导入" valuePropName="checked">
                    <Switch disabled={!canRun} />
                  </Form.Item>
                  <Typography.Paragraph type="secondary">将执行的建表 DDL（只读预览）</Typography.Paragraph>
                  <pre style={{
                    background: '#f7f7f7',
                    border: '1px solid #eee',
                    borderRadius: 6,
                    padding: 12,
                    maxHeight: 280,
                    overflow: 'auto',
                    fontSize: 12,
                  }}
                  >
                    {ddl || '请先选择目标数据源与表名'}
                  </pre>
                  <Button size="small" onClick={refreshDdl}>刷新 DDL</Button>
                </>
              ),
            },
          ]}
        />
      </Form>
    </Drawer>
  )
}

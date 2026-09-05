/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Collapse, Drawer, Form, Input, Progress, Select, Space, Switch, Table, Tabs, Upload,
  message, Typography,
} from 'antd'
import { InboxOutlined, CloudUploadOutlined, LoadingOutlined, StopOutlined } from '@ant-design/icons'
import { datasourceApi, integrationApi } from '../api'
import {
  describeUploadNetworkError,
  FILE_IMPORT_CHUNK_BYTES,
  fileImportClientKey,
  formatUploadEta,
  formatUploadSpeed,
  loadFileImportSession,
} from '../utils/fileImportUpload'

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
  /** 非空时为「重新上传」模式：复用已有 file_import 任务，提交时调 updateTask */
  editTask?: any | null
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
  open, workspaceId, canWrite, canRun, defaultDatasourceId, editTask, onClose, onDone,
}: Props) {
  const [form] = Form.useForm()
  const [tab, setTab] = useState('file')
  const [uploading, setUploading] = useState(false)
  const [uploadPhase, setUploadPhase] = useState<'idle' | 'uploading' | 'parsing'>('idle')
  const [uploadPercent, setUploadPercent] = useState(0)
  const [uploadEtaText, setUploadEtaText] = useState<string | null>(null)
  const [uploadSpeedText, setUploadSpeedText] = useState<string | null>(null)
  const [selectedFileName, setSelectedFileName] = useState<string | null>(null)
  const [selectedFileSize, setSelectedFileSize] = useState(0)
  const [chunkProgress, setChunkProgress] = useState<{ received: number; total: number; resumed: boolean } | null>(null)
  const [activeFileId, setActiveFileId] = useState<string | null>(null)
  const [resumeHint, setResumeHint] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [datasources, setDatasources] = useState<any[]>([])
  const [fileMeta, setFileMeta] = useState<any>(null)
  const [columns, setColumns] = useState<ColDef[]>([])
  const [previewRows, setPreviewRows] = useState<any[][]>([])
  const [ddl, setDdl] = useState('')
  const [tableExists, setTableExists] = useState(false)
  const [schemaDiff, setSchemaDiff] = useState<any>(null)

  const targetDs = useMemo(
    () => datasources.filter((d: any) => ['mysql', 'doris'].includes((d.ds_type || '').toLowerCase())),
    [datasources],
  )

  useEffect(() => {
    if (!open || !workspaceId) return
    datasourceApi.list(workspaceId).then((d: any) => setDatasources(Array.isArray(d) ? d : []))
    const editCfg = editTask?.sync_config || {}
    form.setFieldsValue({
      has_header: editCfg.has_header !== false,
      encoding: editCfg.encoding || undefined,
      delimiter: editCfg.delimiter || undefined,
      register_datamap: editCfg.register_datamap !== false,
      if_exists: 'fail',
      operation_mode: editTask ? 'append' : 'create',
      quality_mode: 'strict',
      run_now: true,
      dst_datasource_id: editTask?.dst_datasource_id || defaultDatasourceId || undefined,
      dst_table: editTask?.dst_table || undefined,
      name: editTask?.name || undefined,
      description: editTask?.description || undefined,
    })
    setTab('file')
    setFileMeta(null)
    setColumns([])
    setPreviewRows([])
    setDdl('')
    setTableExists(false)
    setSchemaDiff(null)
    setUploading(false)
    setUploadPhase('idle')
    setUploadPercent(0)
    setUploadEtaText(null)
    setUploadSpeedText(null)
    setSelectedFileName(null)
    setSelectedFileSize(0)
    setChunkProgress(null)
    setActiveFileId(null)
    setResumeHint(null)
    abortRef.current?.abort()
    abortRef.current = null
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

  const formatBytes = (n: number) => {
    if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
    if (n >= 1024) return `${(n / 1024).toFixed(0)} KB`
    return `${n} B`
  }

  const handleAbortUpload = async () => {
    abortRef.current?.abort()
    if (activeFileId) {
      try {
        await integrationApi.abortFileImportUpload(workspaceId, activeFileId)
      } catch {
        /* ignore */
      }
    }
    setUploading(false)
    setUploadPhase('idle')
    setResumeHint(null)
    message.info({ content: '已取消上传', key: 'file-import-upload' })
  }

  const handleUpload = async (file: File) => {
    if (!canWrite) return false
    setSelectedFileName(file.name)
    setSelectedFileSize(file.size)
    setFileMeta(null)
    setUploading(true)
    setUploadPhase('uploading')
    setUploadPercent(0)
    setUploadEtaText(null)
    setUploadSpeedText(null)
    setChunkProgress(null)
    setResumeHint(null)

    const useChunk = file.size > FILE_IMPORT_CHUNK_BYTES
    const clientKey = fileImportClientKey(workspaceId, file)
    const prior = useChunk ? loadFileImportSession(clientKey) : null
    if (prior?.fileId) {
      setResumeHint(`检测到未完成上传，将从已传分片继续（会话 ${prior.fileId.slice(0, 8)}…）`)
    }

    const ac = new AbortController()
    abortRef.current = ac

    message.loading({
      content: useChunk
        ? (prior
          ? `断点续传 ${file.name}（约 ${formatBytes(file.size)}）…`
          : `正在分片上传 ${file.name}（约 ${formatBytes(file.size)}）…`)
        : `正在上传 ${file.name}…`,
      key: 'file-import-upload',
      duration: 0,
    })
    try {
      const res: any = await integrationApi.uploadFileImport(workspaceId, file, {
        has_header: form.getFieldValue('has_header') !== false,
        encoding: form.getFieldValue('encoding'),
        delimiter: form.getFieldValue('delimiter'),
        sheet_name: form.getFieldValue('sheet_name'),
        signal: ac.signal,
        onProgress: (pct, meta) => {
          setUploadPercent(pct)
          if (meta) {
            setUploadSpeedText(formatUploadSpeed(meta.speedBps) || null)
            setUploadEtaText(formatUploadEta(meta.etaSeconds) || null)
          }
        },
        onStatus: (info) => {
          setActiveFileId(info.fileId)
          setChunkProgress({ received: info.received, total: info.total, resumed: info.resumed })
          if (info.resumed && info.received > 0) {
            setResumeHint(`已跳过 ${info.received}/${info.total} 个已上传分片，继续传输剩余部分`)
          }
        },
        onPhase: (phase) => {
          setUploadPhase(phase)
          if (phase === 'parsing') {
            setUploadEtaText(null)
            setUploadSpeedText(null)
            message.loading({
              content: '分片已齐，服务端正在解析（大文件可能需数分钟）…',
              key: 'file-import-upload',
              duration: 0,
            })
          }
        },
      })
      setUploadPercent(100)
      setUploadPhase('idle')
      setResumeHint(null)
      applyParseResult(res)
      message.success({
        content: `已解析 ${res.row_count ?? 0} 行（${formatBytes(res.size_bytes || file.size)}）`,
        key: 'file-import-upload',
      })
      setTab('schema')
    } catch (e: any) {
      setUploadPhase('idle')
      const tip = describeUploadNetworkError(e)
      setResumeHint(tip)
      message.error({
        content: tip,
        key: 'file-import-upload',
        duration: 10,
      })
    } finally {
      setUploading(false)
      abortRef.current = null
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
      try {
        const diffRes: any = await integrationApi.fileImportSchemaDiff({
          datasource_id: dsId,
          table_name: table,
          columns: columns.map(({ name, type, nullable, is_primary_key }) => ({
            name, type, nullable, is_primary_key,
          })),
        })
        setSchemaDiff(diffRes?.diff || null)
      } catch {
        setSchemaDiff(null)
      }
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

  const canEnterTab = (key: string) => {
    if (key === 'file') return true
    if (!fileMeta?.file_id) return false
    if (key === 'schema') return true
    if (!columns.length) return false
    return true
  }

  const handleTabChange = (key: string) => {
    if (!canEnterTab(key)) {
      if (!fileMeta?.file_id) {
        message.warning('请先完成文件上传')
        setTab('file')
        return
      }
      if (!columns.length) {
        message.warning('请先确认字段定义')
        setTab('schema')
        return
      }
    }
    setTab(key)
  }

  const handleSubmit = async () => {
    if (!canWrite) return
    try {
      const values = await form.validateFields([
        'name', 'dst_datasource_id', 'dst_table', 'operation_mode', 'quality_mode', 'register_datamap', 'run_now',
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
      const op = values.operation_mode || 'create'
      if (op === 'append' && schemaDiff && schemaDiff.compatible === false) {
        message.error('目标表结构不兼容，无法 append；请调整字段或改用 replace')
        setTab('target')
        return
      }
      if (op === 'replace' && tableExists) {
        const ok = window.confirm('replace 将用本次导入结果替换目标表全部数据，确认继续？')
        if (!ok) return
      }
      if (values.run_now && !canRun) {
        message.warning('无运行权限，请取消「立即导入」后仅创建任务')
        return
      }
      setSubmitting(true)
      const cols = columns.map(({ name, type, nullable, is_primary_key }) => ({
        name, type, nullable, is_primary_key,
      }))
      const base = {
        file_id: fileMeta.file_id,
        columns: cols,
        encoding: form.getFieldValue('encoding'),
        delimiter: form.getFieldValue('delimiter'),
        has_header: form.getFieldValue('has_header') !== false,
        sheet_name: form.getFieldValue('sheet_name'),
        operation_mode: op,
        quality_mode: values.quality_mode || 'strict',
        register_datamap: !!values.register_datamap,
        run_now: !!values.run_now,
      }
      let res: any
      if (editTask?.id) {
        await integrationApi.updateTask(editTask.id, {
          name: values.name,
          description: values.description,
          dst_datasource_id: values.dst_datasource_id,
          dst_table: values.dst_table,
        })
        res = await integrationApi.createFileImportVersion(editTask.id, {
          ...base,
          activate: true,
        })
        message.success(res?.record_id ? '已创建新版本并排队执行' : '已创建新版本（未立即运行）')
      } else {
        res = await integrationApi.createFileImportTask({
          workspace_id: workspaceId,
          name: values.name,
          description: values.description,
          dst_datasource_id: values.dst_datasource_id,
          dst_table: values.dst_table,
          ...base,
        })
        message.success(res?.message || '已创建')
      }
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
      title={editTask ? `重新上传 — ${editTask.name}` : '本地文件导入'}
      width={760}
      open={open}
      onClose={() => {
        if (uploading) {
          message.warning('正在上传/解析，请稍候完成后再关闭')
          return
        }
        onClose()
      }}
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
        message="上传 CSV（推荐大文件，最大约 3GB / 500 万行；较大文件自动分片且支持断点续传）或 Excel（≤200MB）。Doris 目标走 Stream Load。导入在后台执行，可在运行历史查看进度。"
      />
      <Form form={form} layout="vertical" disabled={!canWrite}>
        <Tabs
          activeKey={tab}
          onChange={handleTabChange}
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
                    <p className="ant-upload-drag-icon">
                      {uploading ? <LoadingOutlined /> : <InboxOutlined />}
                    </p>
                    <p className="ant-upload-text">
                      {uploading
                        ? (uploadPhase === 'parsing' ? '服务端解析中，请稍候…' : '正在上传，请稍候…')
                        : '点击或拖拽文件到此处'}
                    </p>
                    <p className="ant-upload-hint">
                      大文件请用 CSV（约 ≤3GB）；Excel 仅适合 ≤200MB。UTF-8 / GBK 均可。
                    </p>
                  </Upload.Dragger>
                  {(uploading || selectedFileName) && (
                    <div style={{ marginTop: 12 }}>
                      <Alert
                        type={uploading ? 'info' : (fileMeta ? 'success' : 'warning')}
                        showIcon
                        message={
                          selectedFileName
                            ? `${selectedFileName} · ${formatBytes(selectedFileSize || fileMeta?.size_bytes || 0)}`
                            : '准备上传'
                        }
                        description={
                          uploading
                            ? (uploadPhase === 'parsing'
                              ? '文件已传到服务器，正在抽样推断字段并统计行数（百万行 CSV 可能需数分钟，请勿关闭）。'
                              : [
                                  chunkProgress
                                    ? `分片进度 ${chunkProgress.received}/${chunkProgress.total}`
                                      + `${chunkProgress.resumed ? '（续传）' : ''} · ${uploadPercent}%`
                                    : `正在上传… ${uploadPercent}%`,
                                  uploadSpeedText ? `速率 ${uploadSpeedText}` : null,
                                  uploadEtaText ? `预计剩余 ${uploadEtaText}` : (uploadPercent < 2 ? '预计剩余计算中…' : null),
                                  '失败分片自动重试',
                                ].filter(Boolean).join(' · '))
                            : undefined
                        }
                      />
                      {resumeHint && !uploading && (
                        <Alert
                          style={{ marginTop: 8 }}
                          type="warning"
                          showIcon
                          message={resumeHint}
                          description="请再次选择同一文件（同名、同大小）即可从断点继续，无需从头传。"
                        />
                      )}
                      {uploading && (
                        <>
                          <Progress
                            style={{ marginTop: 8 }}
                            percent={uploadPhase === 'parsing' ? 100 : uploadPercent}
                            status={uploadPhase === 'parsing' ? 'active' : (uploadPercent < 100 ? 'active' : 'normal')}
                            format={() => (uploadPhase === 'parsing' ? '解析中' : `${uploadPercent}%`)}
                          />
                          <Button
                            danger
                            size="small"
                            icon={<StopOutlined />}
                            style={{ marginTop: 8 }}
                            onClick={handleAbortUpload}
                          >
                            取消上传
                          </Button>
                        </>
                      )}
                    </div>
                  )}
                  {fileMeta && !uploading && (
                    <Alert
                      style={{ marginTop: 12 }}
                      type="success"
                      showIcon
                      message={
                        `${fileMeta.original_filename} · ${fileMeta.row_count ?? 0} 行`
                        + `${fileMeta.row_count_estimated ? '（估算）' : ''}`
                        + ` · ${formatBytes(fileMeta.size_bytes || 0)}`
                      }
                      description="下一步将创建 Doris/MySQL 内表并装数；对象存储仅作上传中转。"
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
                  <Form.Item name="operation_mode" label="写入模式" rules={[{ required: true }]}>
                    <Select
                      options={[
                        { label: '创建（表必须不存在）', value: 'create' },
                        { label: '追加（结构须兼容）', value: 'append', disabled: tableExists && schemaDiff?.compatible === false },
                        { label: '替换（覆盖目标表数据）', value: 'replace' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="quality_mode" label="质量模式">
                    <Select
                      options={[
                        { label: '严格（坏行则失败）', value: 'strict' },
                        { label: '宽松（跳过坏行并记录）', value: 'lenient' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="register_datamap" label="导入后注册到数据字典" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                  {tableExists && form.getFieldValue('operation_mode') === 'create' && (
                    <Alert type="warning" showIcon message="目标表已存在，请更换表名或改为追加/替换" />
                  )}
                  {tableExists && schemaDiff && (
                    <Alert
                      style={{ marginTop: 8 }}
                      type={schemaDiff.compatible ? 'success' : 'error'}
                      showIcon
                      message={schemaDiff.compatible ? '与目标表结构兼容' : '与目标表结构不兼容'}
                      description={
                        schemaDiff.compatible
                          ? `目标字段 ${schemaDiff.actual_count}，导入字段 ${schemaDiff.expected_count}`
                          : `缺失 ${JSON.stringify(schemaDiff.missing_in_target)}；类型差异 ${JSON.stringify(schemaDiff.type_mismatch)}`
                      }
                    />
                  )}
                </>
              ),
            },
            {
              key: 'confirm',
              label: '装载与确认',
              children: (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="装载方式：内表写入"
                    description={
                      fileMeta?.storage_note
                      || '创建物理表后通过 Stream Load（Doris）或批量 INSERT（MySQL）写入。不创建 CSV 外表。'
                    }
                  />
                  {!!fileMeta?.s3_uri && (
                    <Collapse
                      style={{ marginBottom: 12 }}
                      items={[{
                        key: 's3',
                        label: '高级：对象存储副本（上传中转）',
                        children: (
                          <>
                            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
                              多副本环境下文件会暂存在对象存储；正式查询请用内表，勿长期依赖该路径。
                            </Typography.Paragraph>
                            <Typography.Paragraph copyable={{ text: fileMeta.s3_uri }} code>
                              {fileMeta.s3_uri}
                            </Typography.Paragraph>
                            {fileMeta.advanced_s3_tvf_hint && (
                              <pre style={{
                                background: '#f7f7f7',
                                border: '1px solid #eee',
                                borderRadius: 6,
                                padding: 12,
                                maxHeight: 200,
                                overflow: 'auto',
                                fontSize: 12,
                              }}
                              >
                                {fileMeta.advanced_s3_tvf_hint}
                              </pre>
                            )}
                          </>
                        ),
                      }]}
                    />
                  )}
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

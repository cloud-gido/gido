/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Table, Button, Space, Tag, message, Modal, Form, Input, InputNumber, Select, Upload, Card, Descriptions,
  Divider, Typography, Alert, notification, Collapse, Popconfirm,
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, StopOutlined, SaveOutlined, ReloadOutlined, UploadOutlined, DeleteOutlined,
  UnlockOutlined, HistoryOutlined,
} from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { streamingApi, approvalApi } from '../api'
import { useAppStore } from '../store'
import { isWorkspaceAdmin } from '../perm'
import PublishApprovalModal from '../components/PublishApprovalModal'
import { approvalPendingKey } from '../approvalLabels'
import EditorAppearanceToolbar from '../components/EditorAppearanceToolbar'
import ResizableSidebar from '../components/ResizableSidebar'
import { R } from '../routes'
import { Link } from 'react-router-dom'
import {
  registerDwMonacoThemes,
  loadEditorAppearance,
  monacoEditorOptionsFromAppearance,
  type EditorAppearance,
} from '../utils/editorAppearance'
import { formatInTimeZone } from '../utils/datetime'
import { openFlinkConsoleUrl } from '../utils/flinkConsole'

const { Paragraph, Text } = Typography

const JOB_TYPES = [
  { label: 'Flink SQL', value: 'SQL' },
  { label: 'JAR 作业', value: 'JAR' },
]

type SqlSubmitMode = 'session' | 'kubernetes_application' | 'flink_operator'

type OperatorResForm = {
  jm_memory: string
  jm_cpu: string
  tm_memory: string
  tm_cpu: string
  task_slots: string
  tm_replicas: string
}

const EMPTY_OPERATOR_RES: OperatorResForm = {
  jm_memory: '',
  jm_cpu: '',
  tm_memory: '',
  tm_cpu: '',
  task_slots: '',
  tm_replicas: '',
}

function parseResourceTier(sp: unknown): string {
  if (sp == null || String(sp).trim() === '') return ''
  try {
    const t = JSON.parse(String(sp))?.resource_tier
    return t != null ? String(t) : ''
  } catch {
    return ''
  }
}

function parseOperatorResForm(sp: unknown): OperatorResForm {
  if (sp == null || String(sp).trim() === '') return { ...EMPTY_OPERATOR_RES }
  try {
    const obj = JSON.parse(String(sp))
    const or = obj?.operator_resources || {}
    return {
      jm_memory: or.jobManager?.memory != null ? String(or.jobManager.memory) : '',
      jm_cpu: or.jobManager?.cpu != null ? String(or.jobManager.cpu) : '',
      tm_memory: or.taskManager?.memory != null ? String(or.taskManager.memory) : '',
      tm_cpu: or.taskManager?.cpu != null ? String(or.taskManager.cpu) : '',
      task_slots: or.taskSlots != null ? String(or.taskSlots) : (or.numberOfTaskSlots != null ? String(or.numberOfTaskSlots) : ''),
      tm_replicas: or.taskManager?.replicas != null ? String(or.taskManager.replicas) : '',
    }
  } catch {
    return { ...EMPTY_OPERATOR_RES }
  }
}

function buildStreamingPropertiesJson(
  rawJson: string,
  operatorForm: OperatorResForm,
  includeOperatorRes: boolean,
  resourceTier?: string,
): string {
  let base: Record<string, unknown> = {}
  const trimmed = rawJson.trim()
  if (trimmed && trimmed !== '{}') {
    base = JSON.parse(trimmed)
    if (typeof base !== 'object' || base === null || Array.isArray(base)) {
      throw new Error('invalid')
    }
  }
  if (includeOperatorRes) {
    const tier = (resourceTier || '').trim()
    if (tier) base.resource_tier = tier
    else delete base.resource_tier
    const or: Record<string, unknown> = {}
    const jm: Record<string, unknown> = {}
    const tm: Record<string, unknown> = {}
    if (operatorForm.jm_memory.trim()) jm.memory = operatorForm.jm_memory.trim()
    if (operatorForm.jm_cpu.trim()) jm.cpu = Number(operatorForm.jm_cpu)
    if (operatorForm.tm_memory.trim()) tm.memory = operatorForm.tm_memory.trim()
    if (operatorForm.tm_cpu.trim()) tm.cpu = Number(operatorForm.tm_cpu)
    if (operatorForm.tm_replicas.trim()) tm.replicas = Number(operatorForm.tm_replicas)
    if (Object.keys(jm).length) or.jobManager = jm
    if (Object.keys(tm).length) or.taskManager = tm
    if (operatorForm.task_slots.trim()) or.taskSlots = Number(operatorForm.task_slots)
    if (Object.keys(or).length) base.operator_resources = or
    else delete base.operator_resources
  }
  if (!Object.keys(base).length) return ''
  return JSON.stringify(base)
}

function sqlModeLabel(mode: string | undefined) {
  const m = (mode || 'flink_operator').toLowerCase()
  if (m === 'kubernetes_application') return 'K8s Application'
  if (m === 'flink_operator') return 'Flink Operator'
  return 'Session'
}

function cdcPaimonSqlTemplate(warehouse: string) {
  const wh = warehouse || 's3://gido-paimon-warehouse'
  return `-- MySQL CDC → Paimon（GIDO 统一运行时 · Flink Operator）
CREATE TABLE mysql_orders (
  order_id BIGINT,
  user_id BIGINT,
  amount DECIMAL(10, 2),
  updated_at TIMESTAMP(3),
  PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'connector' = 'mysql-cdc',
  'hostname' = 'mysql.example.svc',
  'port' = '3306',
  'username' = 'cdc_user',
  'password' = '***',
  'database-name' = 'shop',
  'table-name' = 'orders'
);

CREATE CATALOG paimon WITH (
  'type' = 'paimon',
  'warehouse' = '${wh}'
);

USE CATALOG paimon;

CREATE TABLE IF NOT EXISTS ods.orders (
  order_id BIGINT,
  user_id BIGINT,
  amount DECIMAL(10, 2),
  updated_at TIMESTAMP(3),
  PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
  'bucket' = '4',
  'changelog-producer' = 'input'
);

INSERT INTO ods.orders
SELECT order_id, user_id, amount, updated_at FROM default_catalog.default_database.mysql_orders;
`
}

export default function StreamStudioPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [jobs, setJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<any | null>(null)
  const [scriptDraft, setScriptDraft] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const editorRef = useRef<any>(null)
  const [editorAppearance, setEditorAppearance] = useState<EditorAppearance>(() => loadEditorAppearance())
  const [jarForm, setJarForm] = useState({ main_class: '', program_args: '', parallelism: 1 })
  const [sqlParallelism, setSqlParallelism] = useState(1)
  /** Flink SQL Gateway Open Session 合并用 JSON（对标阿里云实时计算「参数调优」的轻量版） */
  const [streamingPropsJson, setStreamingPropsJson] = useState('{}')
  const [flinkRuntime, setFlinkRuntime] = useState<any | null>(null)
  /** 终态产品：仅 Flink Operator；提交模式不再在 UI 暴露 */
  const [sqlSubmitMode] = useState<SqlSubmitMode>('flink_operator')
  const [operatorResForm, setOperatorResForm] = useState<OperatorResForm>({ ...EMPTY_OPERATOR_RES })
  const [resourceTier, setResourceTier] = useState<string>('')
  const [jarStreamingPropsJson, setJarStreamingPropsJson] = useState('{}')
  const [jarSubmitMode] = useState<'session' | 'flink_operator'>('flink_operator')
  const [historyModal, setHistoryModal] = useState(false)
  const [historyList, setHistoryList] = useState<any[]>([])
  /** 作业绑定的 Flink Session 配置（null = 使用平台集成默认） */
  const [flinkProfileId, setFlinkProfileId] = useState<number | null>(null)
  const [flinkProfiles, setFlinkProfiles] = useState<any[]>([])
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set())
  const [approvalOpen, setApprovalOpen] = useState(false)
  const [approvalNote, setApprovalNote] = useState('')

  const load = useCallback(async (showSpinner = true) => {
    if (!wsId) return
    if (showSpinner) setLoading(true)
    try {
      const [list, pendingRes]: any = await Promise.all([
        streamingApi.listJobs(wsId),
        approvalApi.list(wsId, { status: 'pending', page_size: 200 }),
      ])
      setJobs(list)
      setPendingKeys(
        new Set((pendingRes?.items || []).map((i: any) => approvalPendingKey(i.resource_type, i.resource_id, i.action))),
      )
      try {
        const profs: any = await streamingApi.listFlinkSessionProfiles(wsId)
        setFlinkProfiles(Array.isArray(profs) ? profs : [])
      } catch {
        setFlinkProfiles([])
      }
      setSelected((prev) => {
        if (!prev) return prev
        const fresh = list.find((j: any) => j.id === prev.id)
        return fresh ?? prev
      })
    } finally {
      if (showSpinner) setLoading(false)
    }
  }, [wsId])

  useEffect(() => { load(true) }, [load])

  useEffect(() => {
    streamingApi.flinkRuntime().then(setFlinkRuntime).catch(() => setFlinkRuntime(null))
  }, [])

  const effectiveSqlMode: SqlSubmitMode = 'flink_operator'
  const effectiveJarMode = 'flink_operator' as const

  /** Flink 控制台停止后 JM 已无作业时，单靠列表会卡在 running — 周期性拉 JM 回填平台状态（不打断编辑） */
  useEffect(() => {
    let alive = true
    const tick = async () => {
      if (!wsId || !alive) return
      try {
        const list = (await streamingApi.listJobs(wsId)) as unknown as any[]
        const tracked = list.filter(
          (j: any) =>
            j.status !== 'cancelled'
            && (j.flink_job_id || j.flink_application_cluster_id),
        )
        if (tracked.length === 0) return
        await Promise.all(tracked.map((j: any) => streamingApi.getStatus(j.id).catch(() => null)))
        if (!alive) return
        await load(false)
      } catch { /* ignore */ }
    }
    const iv = window.setInterval(tick, 6500)
    tick()
    return () => {
      alive = false
      window.clearInterval(iv)
    }
  }, [wsId, load])

  useEffect(() => {
    if (selected?.job_type === 'SQL') {
      setScriptDraft(selected.script_content ?? '')
      setSqlParallelism(selected.parallelism ?? 1)
      const sp = selected.streaming_properties
      if (sp != null && String(sp).trim() !== '') {
        try {
          setStreamingPropsJson(JSON.stringify(JSON.parse(String(sp)), null, 2))
        } catch {
          setStreamingPropsJson(String(sp))
        }
      } else {
        setStreamingPropsJson('{}')
      }
      setOperatorResForm(parseOperatorResForm(sp))
      setResourceTier(parseResourceTier(sp))
    }
  }, [selected?.id, selected?.script_content, selected?.job_type, selected?.parallelism, selected?.streaming_properties])

  useEffect(() => {
    if (selected?.job_type === 'JAR') {
      const sp = selected.streaming_properties
      if (sp != null && String(sp).trim() !== '') {
        try {
          setJarStreamingPropsJson(JSON.stringify(JSON.parse(String(sp)), null, 2))
        } catch {
          setJarStreamingPropsJson(String(sp))
        }
      } else {
        setJarStreamingPropsJson('{}')
      }
      setOperatorResForm(parseOperatorResForm(sp))
      setResourceTier(parseResourceTier(sp))
    }
  }, [selected?.id, selected?.job_type, selected?.flink_jar_submit_mode, selected?.streaming_properties])

  useEffect(() => {
    if (!selected?.id) return
    const v = selected.flink_session_profile_id
    setFlinkProfileId(v != null && v !== '' ? Number(v) : null)
  }, [selected?.id, selected?.flink_session_profile_id])

  const handleCreate = async () => {
    const v = await createForm.validateFields()
    await streamingApi.createJob({
      workspace_id: wsId,
      name: v.name,
      job_type: v.job_type,
      script_content: v.job_type === 'SQL' ? (v.script_content || '-- Flink SQL\nCREATE TABLE ...') : null,
      parallelism: v.parallelism ?? 1,
    })
    message.success('已创建任务')
    setCreateOpen(false)
    createForm.resetFields()
    await load()
  }

  const handleUnlock = async () => {
    if (!selected) return
    try {
      await streamingApi.unlockJob(selected.id)
      message.success('已解锁，可继续编辑与提交')
      await load(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '解锁失败')
    }
  }

  const handleSave = async () => {
    if (!selected) return
    if (selected.is_locked) {
      message.warning('作业已锁定，请先解锁后再保存')
      return
    }
    let streaming_properties: string | undefined
    const includeOperatorRes =
      (selected.job_type === 'SQL' && effectiveSqlMode === 'flink_operator')
      || (selected.job_type === 'JAR' && effectiveJarMode === 'flink_operator')
    if (selected.job_type === 'SQL') {
      try {
        streaming_properties = buildStreamingPropertiesJson(streamingPropsJson, operatorResForm, includeOperatorRes, resourceTier)
      } catch {
        message.error('参数调优 JSON 格式无效，请检查')
        return
      }
    } else if (selected.job_type === 'JAR' && effectiveJarMode === 'flink_operator') {
      try {
        streaming_properties = buildStreamingPropertiesJson(jarStreamingPropsJson, operatorResForm, true, resourceTier)
      } catch {
        message.error('高级配置 JSON 格式无效，请检查')
        return
      }
    }
    await streamingApi.updateJob(selected.id, {
      script_content: selected.job_type === 'SQL' ? scriptDraft : undefined,
      main_class: selected.job_type === 'JAR' ? (jarForm.main_class || undefined) : undefined,
      program_args: selected.job_type === 'JAR' ? (jarForm.program_args || undefined) : undefined,
      parallelism: selected.job_type === 'JAR' ? jarForm.parallelism : sqlParallelism,
      flink_session_profile_id: flinkProfileId,
      ...(selected.job_type === 'SQL' ? { streaming_properties, flink_sql_submit_mode: effectiveSqlMode } : {}),
      ...(selected.job_type === 'JAR' ? { flink_jar_submit_mode: effectiveJarMode, streaming_properties } : {}),
    })
    message.success('已保存')
    await load()
  }

  useEffect(() => {
    if (selected?.job_type === 'JAR') {
      setJarForm({
        main_class: selected.main_class ?? '',
        program_args: selected.program_args ?? '',
        parallelism: selected.parallelism ?? 1,
      })
    }
  }, [selected?.id, selected?.job_type])

  const handleSubmit = async () => {
    if (!selected) return
    if (selected.is_locked) {
      message.warning('作业已锁定，请先解锁后再提交')
      return
    }
    if (!canPublishDirect) {
      await handleSave()
      setApprovalNote('')
      setApprovalOpen(true)
      return
    }
    setSubmitting(true)
    try {
      await handleSave()
      const res: any = await streamingApi.submitJob(selected.id, selected.job_type === 'SQL' ? scriptDraft : undefined)
      await load()
      if (res?.submit_warning) {
        message.warning(String(res.submit_warning), 10)
      }
      const desc = res?.flink_console_url
        ? (
            <span>
              <a href={res.flink_console_url} target="_blank" rel="noreferrer">打开 Flink Web UI</a>
              <div style={{ marginTop: 6, fontSize: 12, wordBreak: 'break-all' }}>{res.flink_console_url}</div>
              {' · '}失败排查见 <Link to={R.stream.monitor}>作业运维</Link>
            </span>
          )
        : '未返回控制台链接，请检查后端 FLINK_UI_URL / FLINK_URL。失败原因将写入作业运维中的「启动失败」记录。'
      notification.success({ message: '已提交到 Flink', description: desc, duration: 10 })
    } catch (e: any) {
      const d = e?.response?.data?.detail || '提交失败'
      message.error(typeof d === 'string' ? d : '提交失败')
      await load()
      notification.warning({
        message: '提交失败',
        description: (
          <span>
            详细错误已落库，请在 <Link to={R.stream.monitor}>作业运维</Link> 中打开「诊断」查看启动阶段日志。
          </span>
        ),
        duration: 8,
      })
    } finally {
      setSubmitting(false)
    }
  }

  const submitPublishApproval = async () => {
    if (!selected || !wsId) return
    try {
      await approvalApi.submit({
        workspace_id: wsId,
        resource_type: 'stream_job',
        resource_id: selected.id,
        action: 'submit_job',
        submit_note: approvalNote || undefined,
      })
      message.success('已提交审批，通过后系统将提交到 Flink')
      setApprovalOpen(false)
      setApprovalNote('')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败')
    }
  }

  const isJobPendingApproval = selected
    ? pendingKeys.has(approvalPendingKey('stream_job', selected.id, 'submit_job'))
    : false

  const handleCancelJob = async () => {
    if (!selected) return
    try {
      const res: any = await streamingApi.cancelJob(selected.id)
      message.success(res?.message || '已停止')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '停止失败')
    }
  }

  const openHistory = async () => {
    if (!selected) return
    try {
      const list: any = await streamingApi.getJobHistory(selected.id)
      setHistoryList(Array.isArray(list) ? list : [])
      setHistoryModal(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '加载版本历史失败')
    }
  }

  const handleRollbackHistory = async (historyId: number) => {
    if (!selected) return
    try {
      await streamingApi.rollbackJobHistory(selected.id, historyId)
      message.success('已回滚到该版本')
      setHistoryModal(false)
      await load(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '回滚失败')
    }
  }

  const handleDelete = async (row: any) => {
    Modal.confirm({
      title: '删除实时任务？',
      content: row.name,
      onOk: async () => {
        await streamingApi.deleteJob(row.id)
        message.success('已删除')
        if (selected?.id === row.id) setSelected(null)
        await load()
      },
    })
  }

  const columns = [
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '提交', dataIndex: 'flink_sql_submit_mode', key: 'sm', width: 92,
      render: (_m: string, row: any) => {
        if (row.job_type === 'JAR' || row.job_type === 'SQL') {
          return <Tag color="purple">Operator</Tag>
        }
        return <Text type="secondary">—</Text>
      },
    },
    {
      title: '类型', dataIndex: 'job_type', key: 'job_type', width: 80,
      render: (t: string) => <Tag color={t === 'SQL' ? 'blue' : 'orange'}>{t}</Tag>,
    },
    {
      title: '操作', key: 'op', width: 56,
      render: (_: any, row: any) => (
        <Button type="link" danger size="small" icon={<DeleteOutlined />} onClick={(e) => { e.stopPropagation(); handleDelete(row) }} />
      ),
    },
  ]

  const statusColor: Record<string, string> = {
    draft: 'default', running: 'processing', finished: 'success', failed: 'error', cancelled: 'warning',
  }

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>作业开发</Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 12, maxWidth: 900 }}>
        在此维护<strong>草稿与逻辑</strong>：编辑 SQL / JAR、保存、单作业提交试跑；<strong>版本历史</strong>自动记录保存/提交前的上一版逻辑，可回滚。运行列表、Flink 控制台链接、<strong>提交失败与运行时异常</strong>请使用
        {' '}<Link to={R.stream.monitor}>作业运维</Link>（对标阿里云实时计算等产品中「开发与运维」拆分）。
      </Paragraph>

      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { createForm.resetFields(); setCreateOpen(true) }}>
          新建实时作业
        </Button>
        <Button icon={<ReloadOutlined />} onClick={() => load(true)} loading={loading}>刷新</Button>
      </Space>

      <ResizableSidebar
        storageKey="gido.streamStudio.sidebarWidth"
        defaultWidth={360}
        minWidth={260}
        maxWidth={560}
        style={{ minHeight: 560 }}
        left={(
        <div style={{ height: '100%', minHeight: 560 }}>
          <Table
            size="small"
            rowKey="id"
            loading={loading}
            dataSource={jobs}
            columns={columns}
            pagination={false}
            scroll={{ y: 480 }}
            tableLayout="fixed"
            onRow={row => ({
              onClick: () => setSelected(row),
              style: { cursor: 'pointer', background: selected?.id === row.id ? '#e6f4ff' : undefined },
            })}
          />
        </div>
        )}
        right={(
        <div style={{ height: '100%', minHeight: 560, minWidth: 0 }}>
          {!selected ? (
            <Card>请从左侧选择作业，或新建 Flink SQL / JAR 任务。</Card>
          ) : (
            <Card
              title={
                <Space wrap>
                  <span>{selected.name}</span>
                  <Tag>{selected.job_type}</Tag>
                  {selected.status && (
                    <Tag color={statusColor[selected.status] || 'default'}>{selected.status}</Tag>
                  )}
                  {selected.owner_username && (
                    <Tag>负责人 {selected.owner_username}</Tag>
                  )}
                  {selected.is_locked && <Tag color="orange">已锁定</Tag>}
                </Space>
              }
              extra={
                <Space>
                  {selected.is_locked && (
                    <Button icon={<UnlockOutlined />} onClick={handleUnlock}>解锁</Button>
                  )}
                  <Button icon={<SaveOutlined />} onClick={handleSave} disabled={selected.is_locked}>保存</Button>
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    loading={submitting}
                    onClick={handleSubmit}
                    disabled={selected.is_locked || isJobPendingApproval}
                  >
                    {isJobPendingApproval ? '审批中' : canPublishDirect ? '提交运行' : '提交审批'}
                  </Button>
                  <Popconfirm title="停止该作业？Operator 模式将暂停 FlinkDeployment" onConfirm={handleCancelJob}>
                    <Button danger icon={<StopOutlined />}>停止</Button>
                  </Popconfirm>
                  <Button icon={<ReloadOutlined />} onClick={async () => {
                    try {
                      const s: any = await streamingApi.getStatus(selected.id)
                      const note = s?.note ? ` · ${s.note}` : ''
                      const op = s?.flink_operational?.readiness ? ` · 就绪度 ${s.flink_operational.readiness}` : ''
                      message.info(`状态: ${s.status} / Flink: ${s.flink_status ?? '-'}${op}${note}`)
                      await load()
                    } catch {
                      message.error('同步状态失败')
                    }
                  }}>同步状态</Button>
                  <Button icon={<HistoryOutlined />} onClick={openHistory}>版本历史</Button>
                </Space>
              }
            >
              {selected.last_submit_error && (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="最近一次提交失败（完整内容在「作业运维 → 诊断」）"
                  description={
                    <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0, fontSize: 12, maxHeight: 120, overflow: 'auto' }}>
                      {String(selected.last_submit_error).slice(0, 800)}
                      {(selected.last_submit_error?.length ?? 0) > 800 ? '…' : ''}
                    </pre>
                  }
                />
              )}
              {selected.job_type === 'SQL' && selected.flink_operational?.hints?.length ? (
                <Alert
                  type={selected.flink_operational.readiness === 'blocked' ? 'error' : selected.flink_operational.readiness === 'warning' ? 'warning' : 'info'}
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={`运维就绪度（${selected.flink_operational.readiness}）`}
                  description={(
                    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                      {selected.flink_operational.hints.map((h: string, i: number) => (
                        <li key={i}>{h}</li>
                      ))}
                    </ul>
                  )}
                />
              ) : null}
              <Descriptions size="small" column={2}>
                <Descriptions.Item label="最近提交">
                  {selected.last_submitted_at
                    ? `${formatInTimeZone(selected.last_submitted_at, displayTz)} · ${selected.last_submitted_by_username || '—'}`
                    : '—'}
                </Descriptions.Item>
                <Descriptions.Item label="就绪度">
                  {selected.job_type === 'SQL' && selected.flink_operational?.readiness
                    ? selected.flink_operational.readiness
                    : '—'}
                </Descriptions.Item>
                <Descriptions.Item label="提交模式">
                  {selected.job_type === 'SQL' || selected.job_type === 'JAR'
                    ? 'Flink Operator（统一运行时）'
                    : '—'}
                </Descriptions.Item>
                <Descriptions.Item label="clusterID">{selected.flink_application_cluster_id || '—'}</Descriptions.Item>
                <Descriptions.Item label="Flink Job ID">{selected.flink_job_id || '—'}</Descriptions.Item>
                <Descriptions.Item label="Operator CR">{selected.flink_operator_deployment_name || '—'}</Descriptions.Item>
                <Descriptions.Item label="JAR 标识">{selected.jar_path || '—'}</Descriptions.Item>
                <Descriptions.Item label="Flink Web UI" span={2}>
                  {selected.flink_console_url ? (
                    <>
                      <Button
                        type="link"
                        size="small"
                        style={{ padding: 0, height: 'auto' }}
                        onClick={() => openFlinkConsoleUrl(selected.flink_console_url, selected.id)}
                      >
                        {selected.flink_jar_submit_mode === 'flink_operator' || selected.flink_console_mode === 'operator'
                          ? '打开 K8s 作业 Flink UI'
                          : '打开 Flink Web UI（作业详情）'}
                      </Button>
                      {selected.flink_k8s_jm_service && (
                        <div style={{ marginTop: 4, fontSize: 12, color: 'var(--ant-color-text-secondary)' }}>
                          K8s Service：<code>{selected.flink_k8s_jm_service}</code>
                        </div>
                      )}
                      <div style={{ marginTop: 4, fontSize: 12, color: 'var(--ant-color-text-secondary)', wordBreak: 'break-all' }}>
                        {selected.flink_console_url}
                      </div>
                      {selected.flink_ui_port_forward_hint && (
                        <Alert
                          type="info"
                          showIcon
                          style={{ marginTop: 8 }}
                          message="Kind 本机须先 port-forward"
                          description={(
                            <>
                              在终端执行后保持窗口不关，再点上方链接：
                              <pre style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap' }}>{selected.flink_ui_port_forward_hint}</pre>
                            </>
                          )}
                        />
                      )}
                    </>
                  ) : (selected.job_type === 'JAR' && selected.flink_jar_submit_mode === 'flink_operator')
                    || (selected.job_type === 'SQL' && selected.flink_sql_submit_mode === 'flink_operator') ? (
                    <Text type="secondary">提交成功后点链接即可（经 GIDO 代理，无需 port-forward JM）</Text>
                  ) : (
                    <Text type="secondary">提交成功后将生成链接</Text>
                  )}
                </Descriptions.Item>
              </Descriptions>
              <Divider style={{ margin: '12px 0' }} />

              {selected.job_type === 'SQL' ? (
                <>
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 8 }}
                    message="统一运行时 · Flink Operator + gido-flink-runtime（Paimon / CDC）"
                    description={(
                      <div style={{ fontSize: 13 }}>
                        <div>Flink {flinkRuntime?.flink_version || '2.0.1'} · 命名空间 {flinkRuntime?.operator_namespace || 'flink'}</div>
                        {flinkRuntime?.paimon_warehouse_default && (
                          <div>默认 Paimon warehouse：<code>{flinkRuntime.paimon_warehouse_default}</code></div>
                        )}
                        {flinkRuntime?.connectors?.length ? (
                          <div style={{ marginTop: 4 }}>
                            预置连接器：{flinkRuntime.connectors.map((c: any) => `${c.name} ${c.version}`).join(' · ')}
                          </div>
                        ) : null}
                      </div>
                    )}
                  />
                  <div style={{ marginBottom: 8, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                    <Space>
                      <span style={{ marginRight: 8 }}>并行度</span>
                      <InputNumber min={1} value={sqlParallelism} onChange={v => setSqlParallelism(Number(v) || 1)} disabled={selected.is_locked} />
                    </Space>
                    <Tag color="purple">Flink Operator</Tag>
                    <Button
                      size="small"
                      disabled={selected.is_locked}
                      onClick={() => setScriptDraft(cdcPaimonSqlTemplate(flinkRuntime?.paimon_warehouse_default || ''))}
                    >
                      插入 CDC→Paimon 模板
                    </Button>
                    <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
                  </div>
                  {effectiveSqlMode === 'flink_operator' && (
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 8 }}
                      message="FlinkDeployment Application + SQL Runner"
                      description={(
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                          <li>统一镜像含 <code>sql-runner.jar</code>、Paimon、MySQL/Postgres CDC。</li>
                          <li>SQL 脚本经 ConfigMap 挂载；资源在下方「Operator 资源配置」按作业覆盖。</li>
                        </ul>
                      )}
                    />
                  )}
                  <Collapse
                    ghost
                    style={{ marginBottom: 8 }}
                    items={[
                      ...(effectiveSqlMode === 'flink_operator' ? [{
                        key: 'operator-res',
                        label: 'Operator 资源配置（JM / TM / Slots，留空用平台默认）',
                        children: (
                          <div>
                            <Form.Item label="规格模板" style={{ marginBottom: 12, maxWidth: 360 }}>
                              <Select
                                allowClear
                                placeholder="平台默认（不套用模板）"
                                value={resourceTier || undefined}
                                disabled={selected.is_locked}
                                onChange={v => setResourceTier(v || '')}
                                options={[
                                  { value: 'small', label: '小 — 轻量 SQL / 探查' },
                                  { value: 'medium', label: '中 — 默认生产' },
                                  { value: 'large', label: '大 — 高并行 / 重 SQL' },
                                ]}
                              />
                            </Form.Item>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
                            <Form.Item label="JM 内存" style={{ marginBottom: 0 }}>
                              <Input placeholder="2048m" value={operatorResForm.jm_memory} disabled={selected.is_locked}
                                onChange={e => setOperatorResForm(f => ({ ...f, jm_memory: e.target.value }))} />
                            </Form.Item>
                            <Form.Item label="JM CPU" style={{ marginBottom: 0 }}>
                              <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                                value={operatorResForm.jm_cpu ? Number(operatorResForm.jm_cpu) : undefined}
                                disabled={selected.is_locked}
                                onChange={v => setOperatorResForm(f => ({ ...f, jm_cpu: v != null ? String(v) : '' }))} />
                            </Form.Item>
                            <Form.Item label="TM 内存" style={{ marginBottom: 0 }}>
                              <Input placeholder="4096m" value={operatorResForm.tm_memory} disabled={selected.is_locked}
                                onChange={e => setOperatorResForm(f => ({ ...f, tm_memory: e.target.value }))} />
                            </Form.Item>
                            <Form.Item label="TM CPU" style={{ marginBottom: 0 }}>
                              <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                                value={operatorResForm.tm_cpu ? Number(operatorResForm.tm_cpu) : undefined}
                                disabled={selected.is_locked}
                                onChange={v => setOperatorResForm(f => ({ ...f, tm_cpu: v != null ? String(v) : '' }))} />
                            </Form.Item>
                            <Form.Item label="Task Slots" style={{ marginBottom: 0 }}>
                              <InputNumber min={1} style={{ width: '100%' }} placeholder="2"
                                value={operatorResForm.task_slots ? Number(operatorResForm.task_slots) : undefined}
                                disabled={selected.is_locked}
                                onChange={v => setOperatorResForm(f => ({ ...f, task_slots: v != null ? String(v) : '' }))} />
                            </Form.Item>
                            <Form.Item label="TM 副本数" style={{ marginBottom: 0 }}>
                              <InputNumber min={1} style={{ width: '100%' }} placeholder="自动"
                                value={operatorResForm.tm_replicas ? Number(operatorResForm.tm_replicas) : undefined}
                                disabled={selected.is_locked}
                                onChange={v => setOperatorResForm(f => ({ ...f, tm_replicas: v != null ? String(v) : '' }))} />
                            </Form.Item>
                            </div>
                          </div>
                        ),
                      }] : []),
                      {
                        key: 'tuning',
                        label: effectiveSqlMode === 'flink_operator'
                          ? '高级 Flink 配置（合并进 FlinkDeployment flinkConfiguration）'
                          : '参数调优（Flink SQL Gateway 会话级，类似实时计算高级配置）',
                        children: (
                          <div>
                            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
                              {effectiveSqlMode === 'flink_operator'
                                ? '填写 JSON：顶级键（除 operator_resources）合并进 FlinkDeployment flinkConfiguration。operator_resources 请用上方面板。'
                                : '填写 JSON 对象：普通键值合并进 Gateway Open Session 的 properties。K8s Application 可在顶级增加 k8s_application 覆盖 executionConfig。'}
                            </Paragraph>
                            <Input.TextArea
                              rows={8}
                              value={streamingPropsJson}
                              onChange={e => setStreamingPropsJson(e.target.value)}
                              disabled={selected.is_locked}
                              style={{ fontFamily: 'monospace', fontSize: 12 }}
                              placeholder={'{\n  "execution.checkpointing.interval": "60000"\n}'}
                            />
                          </div>
                        ),
                      },
                    ]}
                  />
                  <div style={{ border: '1px solid #f0f0f0', borderRadius: 8, overflow: 'hidden' }}>
                  <Editor
                    height="420px"
                    language="sql"
                    theme={editorAppearance.theme}
                    value={scriptDraft}
                    onChange={selected.is_locked ? undefined : (v => setScriptDraft(v ?? ''))}
                    beforeMount={registerDwMonacoThemes}
                    onMount={ed => { editorRef.current = ed }}
                    options={{ ...monacoEditorOptionsFromAppearance(editorAppearance), readOnly: Boolean(selected.is_locked) }}
                  />
                  </div>
                </>
              ) : (
                <Space direction="vertical" style={{ width: '100%' }} size="middle">
                  <Alert
                    type="info"
                    showIcon
                    message="统一运行时 · Flink Operator + gido-flink-runtime"
                    description="JAR 作业通过 FlinkDeployment Application 提交；制品由 GIDO backend 提供 HTTP 拉取。"
                  />
                  <Tag color="purple">Flink Operator</Tag>
                  {effectiveJarMode === 'flink_operator' && (
                    <>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 8 }}
                      message="Flink Operator 生产"
                      description="生产环境请配置 GIDO_FLINK_OPERATOR_UI_URL_TEMPLATE（Ingress 域名）或 LoadBalancer；本机 Kind 开发设 GIDO_FLINK_OPERATOR_DEV_LOCAL=true 并按提示 port-forward。"
                    />
                    <Alert
                      type="info"
                      showIcon
                      message="Operator 生产提交"
                      description={(
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                          <li>上传 JAR 会写入 GIDO 制品库；Flink Pod 通过 HTTP 拉取（后续可切 S3）。</li>
                          <li>须填写 <strong>Main Class</strong>；Backend 容器需挂载 kubeconfig。</li>
                          <li>默认 namespace：<code>flink</code>（Kind 集群 <code>kind-gido</code>），Flink 2.0.1 + Operator 1.15。</li>
                        </ul>
                      )}
                    />
                    </>
                  )}
                  <Upload
                    disabled={selected.is_locked}
                    maxCount={1}
                    beforeUpload={async file => {
                      if (!file.name.endsWith('.jar')) {
                        message.error('请上传 .jar')
                        return Upload.LIST_IGNORE
                      }
                      try {
                        await streamingApi.uploadJar(selected.id, file)
                        message.success('JAR 已上传至 Flink')
                        await load()
                      } catch (e: any) {
                        message.error(e?.response?.data?.detail || '上传失败')
                      }
                      return false
                    }}
                    showUploadList={false}
                  >
                    <Button icon={<UploadOutlined />}>上传 JAR{effectiveJarMode === 'flink_operator' ? '（制品库）' : ' 到 Flink'}</Button>
                  </Upload>
                  {effectiveJarMode === 'flink_operator' && (
                    <Collapse
                      ghost
                      style={{ marginBottom: 8 }}
                      items={[
                        {
                          key: 'operator-res',
                          label: 'Operator 资源配置（JM / TM / Slots，留空用平台默认）',
                          children: (
                            <div>
                              <Form.Item label="规格模板" style={{ marginBottom: 12, maxWidth: 360 }}>
                                <Select
                                  allowClear
                                  placeholder="平台默认"
                                  value={resourceTier || undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setResourceTier(v || '')}
                                  options={[
                                    { value: 'small', label: '小 — 轻量作业' },
                                    { value: 'medium', label: '中 — 默认生产' },
                                    { value: 'large', label: '大 — 高资源' },
                                  ]}
                                />
                              </Form.Item>
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
                              <Form.Item label="JM 内存" style={{ marginBottom: 0 }}>
                                <Input placeholder="2048m" value={operatorResForm.jm_memory} disabled={selected.is_locked}
                                  onChange={e => setOperatorResForm(f => ({ ...f, jm_memory: e.target.value }))} />
                              </Form.Item>
                              <Form.Item label="JM CPU" style={{ marginBottom: 0 }}>
                                <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                                  value={operatorResForm.jm_cpu ? Number(operatorResForm.jm_cpu) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, jm_cpu: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              <Form.Item label="TM 内存" style={{ marginBottom: 0 }}>
                                <Input placeholder="4096m" value={operatorResForm.tm_memory} disabled={selected.is_locked}
                                  onChange={e => setOperatorResForm(f => ({ ...f, tm_memory: e.target.value }))} />
                              </Form.Item>
                              <Form.Item label="TM CPU" style={{ marginBottom: 0 }}>
                                <InputNumber min={0.1} step={0.5} style={{ width: '100%' }} placeholder="1"
                                  value={operatorResForm.tm_cpu ? Number(operatorResForm.tm_cpu) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, tm_cpu: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              <Form.Item label="Task Slots" style={{ marginBottom: 0 }}>
                                <InputNumber min={1} style={{ width: '100%' }} placeholder="2"
                                  value={operatorResForm.task_slots ? Number(operatorResForm.task_slots) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, task_slots: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              <Form.Item label="TM 副本数" style={{ marginBottom: 0 }}>
                                <InputNumber min={1} style={{ width: '100%' }} placeholder="自动"
                                  value={operatorResForm.tm_replicas ? Number(operatorResForm.tm_replicas) : undefined}
                                  disabled={selected.is_locked}
                                  onChange={v => setOperatorResForm(f => ({ ...f, tm_replicas: v != null ? String(v) : '' }))} />
                              </Form.Item>
                              </div>
                            </div>
                          ),
                        },
                        {
                          key: 'advanced',
                          label: '高级 Flink 配置（合并进 flinkConfiguration）',
                          children: (
                            <Input.TextArea
                              rows={6}
                              value={jarStreamingPropsJson}
                              onChange={e => setJarStreamingPropsJson(e.target.value)}
                              disabled={selected.is_locked}
                              style={{ fontFamily: 'monospace', fontSize: 12 }}
                              placeholder={'{\n  "execution.checkpointing.interval": "60s"\n}'}
                            />
                          ),
                        },
                      ]}
                    />
                  )}
                  <Form layout="vertical" style={{ maxWidth: 560 }}>
                    <Form.Item label="入口类 (Main Class)">
                      <Input
                        value={jarForm.main_class}
                        placeholder="com.example.StreamingJob"
                        disabled={selected.is_locked}
                        onChange={e => setJarForm(f => ({ ...f, main_class: e.target.value }))}
                      />
                    </Form.Item>
                    <Form.Item label="运行参数">
                      <Input
                        value={jarForm.program_args}
                        placeholder="--key value"
                        disabled={selected.is_locked}
                        onChange={e => setJarForm(f => ({ ...f, program_args: e.target.value }))}
                      />
                    </Form.Item>
                    <Form.Item label="并行度">
                      <InputNumber
                        min={1}
                        value={jarForm.parallelism}
                        disabled={selected.is_locked}
                        onChange={v => setJarForm(f => ({ ...f, parallelism: Number(v) || 1 }))}
                      />
                    </Form.Item>
                  </Form>
                </Space>
              )}
            </Card>
          )}
        </div>
        )}
      />

      <Modal title="新建实时作业" open={createOpen} onOk={handleCreate} onCancel={() => setCreateOpen(false)} destroyOnClose>
        <Form form={createForm} layout="vertical" initialValues={{ job_type: 'SQL', parallelism: 1 }}>
          <Form.Item name="name" label="作业名称" rules={[{ required: true }]}>
            <Input placeholder="例如 ods_user_kafka_to_hudi" />
          </Form.Item>
          <Form.Item name="job_type" label="类型" rules={[{ required: true }]}>
            <Select options={JOB_TYPES} />
          </Form.Item>
          <Form.Item name="parallelism" label="默认并行度">
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.job_type !== c.job_type}>
            {({ getFieldValue }) =>
              getFieldValue('job_type') === 'SQL' ? (
                <Form.Item name="script_content" label="初始 SQL（可选）">
                  <Input.TextArea rows={6} placeholder="留空则创建为模板注释" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="版本历史" open={historyModal} onCancel={() => setHistoryModal(false)} footer={null} width={780} destroyOnClose>
        {historyList.length === 0 && (
          <div style={{ color: '#bbb', textAlign: 'center', padding: 24 }}>
            暂无版本快照。保存时对 SQL / JAR 参数 / 并行度的修改、以及提交运行（SQL 正文变更或 JAR 提交）前，会自动保留上一版内容。
          </div>
        )}
        {historyList.map((h: any) => (
          <div key={h.id} style={{ marginBottom: 12, border: '1px solid #f0f0f0', borderRadius: 4, padding: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <span style={{ color: '#666', fontSize: 12 }}>
                {String(h.saved_at)} · {h.saved_by_username || '—'} · <Tag>{h.job_type}</Tag>
                {h.parallelism != null && <Tag>并行度 {h.parallelism}</Tag>}
                {h.job_type === 'SQL' && (
                  <Tag color={(h.flink_sql_submit_mode || 'flink_operator') === 'flink_operator' ? 'purple' : 'geekblue'}>
                    {(h.flink_sql_submit_mode || 'flink_operator') === 'flink_operator' ? 'Operator' : h.flink_sql_submit_mode === 'kubernetes_application' ? 'Application' : 'Session'}
                  </Tag>
                )}
              </span>
              <Button size="small" onClick={() => handleRollbackHistory(h.id)} disabled={Boolean(selected?.is_locked)}>
                回滚到此版本
              </Button>
            </div>
            {h.job_type === 'SQL' ? (
              <pre style={{ background: '#f5f5f5', padding: 8, borderRadius: 4, fontSize: 12, maxHeight: 180, overflow: 'auto', margin: 0, whiteSpace: 'pre-wrap' }}>
                {(h.script_content || '').slice(0, 2500)}{(h.script_content?.length ?? 0) > 2500 ? '...' : ''}
              </pre>
            ) : (
              <div style={{ fontSize: 12, color: '#555' }}>
                <div><strong>Main:</strong> {h.main_class || '—'}</div>
                <div><strong>Args:</strong> {h.program_args || '—'}</div>
              </div>
            )}
          </div>
        ))}
      </Modal>

      <PublishApprovalModal
        open={approvalOpen}
        title={`提交发布审批 — ${selected?.name || ''}`}
        hint="普通开发不能直接提交到 Flink 生产集群。审批通过后将使用当前已保存的作业定义提交运行。"
        note={approvalNote}
        onNoteChange={setApprovalNote}
        onCancel={() => { setApprovalOpen(false); setApprovalNote('') }}
        onSubmit={submitPublishApproval}
      />
    </div>
  )
}

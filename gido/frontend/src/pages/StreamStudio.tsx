/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import {
  Table, Button, Space, Tag, message, Modal, Form, Input, InputNumber, Select, Upload, Card, Descriptions,
  Divider, Typography, Alert, notification, Collapse,
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

const { Paragraph, Text } = Typography

const JOB_TYPES = [
  { label: 'Flink SQL', value: 'SQL' },
  { label: 'JAR 作业', value: 'JAR' },
]

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
  /** SQL 提交：session=已有集群；kubernetes_application=Gateway v4 起 K8s Application（须配置 FLINK_K8S_*） */
  const [sqlSubmitMode, setSqlSubmitMode] = useState<'session' | 'kubernetes_application'>('session')
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

  /** Flink 控制台停止后 JM 已无作业时，单靠列表会卡在 running — 周期性拉 JM 回填平台状态（不打断编辑） */
  useEffect(() => {
    let alive = true
    const tick = async () => {
      if (!wsId || !alive) return
      try {
        const list = (await streamingApi.listJobs(wsId)) as unknown as any[]
        const tracked = list.filter((j: any) => j.flink_job_id || j.flink_application_cluster_id)
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
      setSqlSubmitMode(selected.flink_sql_submit_mode === 'kubernetes_application' ? 'kubernetes_application' : 'session')
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
    }
  }, [selected?.id, selected?.script_content, selected?.job_type, selected?.parallelism, selected?.streaming_properties, selected?.flink_sql_submit_mode])

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
    if (selected.job_type === 'SQL') {
      const raw = streamingPropsJson.trim()
      if (!raw || raw === '{}') {
        streaming_properties = '' // 清空库内调优参数
      } else {
        try {
          streaming_properties = JSON.stringify(JSON.parse(raw))
        } catch {
          message.error('参数调优 JSON 格式无效，请检查')
          return
        }
      }
    }
    await streamingApi.updateJob(selected.id, {
      script_content: selected.job_type === 'SQL' ? scriptDraft : undefined,
      main_class: selected.job_type === 'JAR' ? (jarForm.main_class || undefined) : undefined,
      program_args: selected.job_type === 'JAR' ? (jarForm.program_args || undefined) : undefined,
      parallelism: selected.job_type === 'JAR' ? jarForm.parallelism : sqlParallelism,
      flink_session_profile_id: flinkProfileId,
      ...(selected.job_type === 'SQL' ? { streaming_properties, flink_sql_submit_mode: sqlSubmitMode } : {}),
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
      const res: any = await streamingApi.submitJob(selected.id, selected.job_type === 'SQL' ? scriptDraft : undefined)
      await load()
      if (res?.submit_warning) {
        message.warning(String(res.submit_warning), 10)
      }
      const desc = res?.flink_console_url
        ? (
            <span>
              <a href={res.flink_console_url} target="_blank" rel="noreferrer">在 Flink Web UI 中查看作业</a>
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
    await streamingApi.cancelJob(selected.id)
    message.success('已请求停止')
    await load()
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
      render: (m: string, row: any) =>
        row.job_type !== 'SQL' ? <Text type="secondary">—</Text> : (
          <Tag color={m === 'kubernetes_application' ? 'purple' : 'geekblue'}>
            {m === 'kubernetes_application' ? 'App' : 'Session'}
          </Tag>
        ),
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
                  <Button danger icon={<StopOutlined />} onClick={handleCancelJob}>停止</Button>
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
              <div style={{ marginBottom: 12, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
                <span style={{ color: '#666' }}>Flink 集群连接</span>
                <Select
                  style={{ minWidth: 280 }}
                  placeholder="默认（平台）"
                  allowClear
                  disabled={selected.is_locked}
                  value={flinkProfileId ?? undefined}
                  onChange={v => setFlinkProfileId(v === undefined || v === null ? null : Number(v))}
                  options={flinkProfiles.map((p: any) => ({ value: p.id, label: `${p.name} (#${p.id})` }))}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  在 <Link to={R.stream.flinkSessions}>Flink 集群连接</Link> 中维护命名连接（继承平台默认，仅覆写不同项）；此处选择后保存。
                </Text>
              </div>
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
                  {selected.job_type === 'SQL'
                    ? (selected.flink_sql_submit_mode === 'kubernetes_application' ? 'K8s Application' : 'Session（已有集群）')
                    : '—'}
                </Descriptions.Item>
                <Descriptions.Item label="Flink 集群连接" span={2}>
                  {selected.flink_session_profile_name
                    ? `${selected.flink_session_profile_name} (#${selected.flink_session_profile_id})`
                    : '默认（平台）'}
                </Descriptions.Item>
                <Descriptions.Item label="clusterID">{selected.flink_application_cluster_id || '—'}</Descriptions.Item>
                <Descriptions.Item label="Flink Job ID">{selected.flink_job_id || '—'}</Descriptions.Item>
                <Descriptions.Item label="JAR 标识">{selected.jar_path || '—'}</Descriptions.Item>
                <Descriptions.Item label="Flink Web UI" span={2}>
                  {selected.flink_console_url ? (
                    <a href={selected.flink_console_url} target="_blank" rel="noreferrer">打开作业详情</a>
                  ) : (
                    <Text type="secondary">提交成功后将生成链接（需配置 FLINK_UI_URL）</Text>
                  )}
                </Descriptions.Item>
              </Descriptions>
              <Divider style={{ margin: '12px 0' }} />

              {selected.job_type === 'SQL' ? (
                <>
                  <div style={{ marginBottom: 8, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                    <Space>
                      <span style={{ marginRight: 8 }}>并行度</span>
                      <InputNumber min={1} value={sqlParallelism} onChange={v => setSqlParallelism(Number(v) || 1)} disabled={selected.is_locked} />
                    </Space>
                    <Space>
                      <span>提交到</span>
                      <Select
                        style={{ minWidth: 260 }}
                        value={sqlSubmitMode}
                        disabled={selected.is_locked}
                        onChange={v => setSqlSubmitMode(v as 'session' | 'kubernetes_application')}
                        options={[
                          { value: 'session', label: 'Session（已有 Flink 集群 / JM）' },
                          { value: 'kubernetes_application', label: 'K8s Application（每作业独立集群）' },
                        ]}
                      />
                    </Space>
                    <EditorAppearanceToolbar value={editorAppearance} onChange={setEditorAppearance} />
                  </div>
                  {sqlSubmitMode === 'kubernetes_application' && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 8 }}
                      message="生产级 Application 提交检查清单"
                      description={(
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                          <li>运行平台已设置 <code>FLINK_K8S_APPLICATION_IMAGE</code>（否则提交将返回 400）。</li>
                          <li>SQL Gateway 支持 <code>/v4/sessions</code>（通常为 Flink 2.x）。</li>
                          <li>建议设置 <code>FLINK_K8S_APPLICATION_JM_REST_TEMPLATE</code>（含 {'{cluster_id}'}）以便回填 Job ID、运维页停止与状态轮询。</li>
                          <li>在「参数调优」中可用顶级 <code>k8s_application</code> 覆盖 executionConfig（资源类、镜像引用等）。</li>
                        </ul>
                      )}
                    />
                  )}
                  <Collapse
                    ghost
                    style={{ marginBottom: 8 }}
                    items={[
                      {
                        key: 'tuning',
                        label: '参数调优（Flink SQL Gateway 会话级，类似实时计算高级配置）',
                        children: (
                          <div>
                            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
                              填写 JSON 对象：普通键值合并进 Gateway Open Session 的 properties（与并行度等一起生效）。
                              若选择「K8s Application」提交，可在顶级增加 <code>k8s_application</code> 对象，其键值会合并进 Flink deploy-script 的 executionConfig（如覆盖
                              <code>kubernetes.container.image.ref</code>、<code>jobmanager.memory.process.size</code> 等）。
                              环境变量还需配置 <code>FLINK_K8S_APPLICATION_IMAGE</code>，建议配置 <code>FLINK_K8S_APPLICATION_JM_REST_TEMPLATE</code>（含 {'{cluster_id}'}）以自动回填 jobId。
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
                    <Button icon={<UploadOutlined />}>上传 JAR 到 Flink</Button>
                  </Upload>
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
                {h.job_type === 'SQL' && (h.flink_sql_submit_mode === 'kubernetes_application'
                  ? <Tag color="purple">Application</Tag>
                  : <Tag color="geekblue">Session</Tag>)}
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

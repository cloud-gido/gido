/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useRef } from 'react'
import {
  Table, Button, Modal, Form, Input, Select, Tag, Space, message,
  Drawer, DatePicker, Tooltip, Popconfirm, Tabs, Alert
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, DeleteOutlined, EyeOutlined,
  ReloadOutlined, HistoryOutlined, CalendarOutlined, EditOutlined,
  CloudUploadOutlined, LinkOutlined,
} from '@ant-design/icons'
import { workflowApi, studioApi, approvalApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'
import { isWorkspaceAdmin } from '../perm'
import DAGEditor, { DAGEditorRef } from '../components/DAGEditor'
import NodeConfigModal from '../components/NodeConfigModal'
import CronBuilder from '../components/CronBuilder'
import { useResizableTableColumns } from '../hooks/useResizableTableColumns'

const STATUS_COLOR: Record<string, string> = {
  success: 'green', failed: 'red', running: 'blue', pending: 'orange', killed: 'default'
}

const WORKFLOW_STATUS: Record<string, { label: string; color: string; desc: string }> = {
  draft: { label: '草稿', color: 'default', desc: '仅保存在 GIDO，尚未发布生产' },
  published: { label: '已上线', color: 'green', desc: '已发布到生产调度，可周期触发' },
  paused: { label: '已暂停', color: 'orange', desc: '生产定义保留，周期调度已暂停' },
  offline: { label: '已下线', color: 'red', desc: '生产调度已下线，历史实例保留' },
}

export default function WorkflowPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const canPublishDirect = isWorkspaceAdmin(user, currentWorkspace)
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [workflows, setWorkflows] = useState<any[]>([])
  const [nodes, setNodes] = useState<any[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editingWf, setEditingWf] = useState<any>(null)
  const [dagConfig, setDagConfig] = useState<any>({ nodes: [], edges: [] })
  const dagEditorRef = useRef<DAGEditorRef>(null)
  const [instanceDrawer, setInstanceDrawer] = useState(false)
  const [batchModal, setBatchModal] = useState(false)
  const [logModal, setLogModal] = useState(false)
  const [logContent, setLogContent] = useState<any[]>([])
  const [selectedWf, setSelectedWf] = useState<any>(null)
  const [instances, setInstances] = useState<any[]>([])
  const [form] = Form.useForm()
  const [batchForm] = Form.useForm()
  const [scheduleType, setScheduleType] = useState<string>('manual')
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set())
  const [approvalModal, setApprovalModal] = useState<any>(null)
  const [approvalNote, setApprovalNote] = useState('')
  const [nodeConfigId, setNodeConfigId] = useState<number | null>(null)
  const [nodeConfigOpen, setNodeConfigOpen] = useState(false)

  const load = async () => {
    if (!wsId) return
    const [wfs, ns, pendingRes]: any = await Promise.all([
      workflowApi.list(wsId),
      studioApi.listNodes(wsId),
      approvalApi.list(wsId, { status: 'pending', page_size: 200 }),
    ])
    setWorkflows(wfs as any[])
    setNodes(ns as any[])
    setPendingKeys(
      new Set((pendingRes?.items || []).map((i: any) => `${i.resource_type}:${i.resource_id}:${i.action}`)),
    )
  }

  useEffect(() => { load() }, [wsId])

  const openCreate = () => {
    setEditingWf(null)
    setDagConfig({ nodes: [], edges: [] })
    form.resetFields()
    setScheduleType('manual')
    setModalOpen(true)
  }

  const openEdit = (wf: any) => {
    setEditingWf(wf)
    setDagConfig(wf.dag_config || { nodes: [], edges: [] })
    form.setFieldsValue(wf)
    setScheduleType(wf.schedule_type || 'manual')
    setModalOpen(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      values.workspace_id = wsId
      if (values.schedule_type !== 'cron') {
        values.cron_expression = null
      }
      const fromEditor = dagEditorRef.current?.getDAG() ?? dagConfig
      // 编辑器只产出 nodes/edges；调度映射绑定生产版本，不写进草稿 DAG。
      const prevDag = editingWf?.dag_config || {}
      values.dag_config = { ...prevDag, ...fromEditor }
      if (editingWf) {
        await workflowApi.update(editingWf.id, values)
        message.success('更新成功')
      } else {
        await workflowApi.create(values)
        message.success('创建成功')
      }
      setModalOpen(false)
      load()
    } catch (e: any) {
      if (e?.errorFields) return // 表单校验失败，antd 已高亮
      const d = e?.response?.data?.detail
      const msg = Array.isArray(d)
        ? d.map((x: any) => x.msg || JSON.stringify(x)).join('; ')
        : (typeof d === 'string' ? d : e?.message || '保存失败')
      message.error(msg)
    }
  }

  const handleRun = async (wf: any) => {
    message.loading({ content: '提交运行中...', key: 'run' })
    try {
      const res: any = await workflowApi.run(wf.id)
      if (res.ds_instance_id) {
        message.success({ content: `已提交到生产调度 (调度实例: ${res.scheduler_instance_id || res.ds_instance_id})`, key: 'run' })
      } else if (res.status === 'success') {
        message.success({ content: '执行成功', key: 'run' })
      } else {
        message.error({ content: `执行失败: ${res.errors?.join(', ')}`, key: 'run' })
      }
    } catch (e: any) {
      message.error({ content: e?.response?.data?.detail || '执行失败', key: 'run' })
    }
    load()
  }

  const handlePublishToDS = async (wf: any) => {
    message.loading({ content: '发布到生产调度...', key: 'ds' })
    try {
      const res: any = await workflowApi.publishToDS(wf.id)
      const sync = Array.isArray(res?.ds_task_sync) ? res.ds_task_sync : []
      const syncHint = sync.length
        ? sync.map((d: any) => `节点#${d.node_id}→${d.ds_task_type}${d.jdbc_type ? `(${d.jdbc_type})` : ''}`).join('；')
        : ''
      message.success({
        content: `已发布生产版本 v${res.version_no || '-'} (definition: ${res.scheduler_definition_id})${syncHint ? ` · ${syncHint}` : ''}`,
        key: 'ds',
      })
      if (res.dolphin_workflow_url) {
        message.info(
          <span>
            诊断入口：
            <a href={res.dolphin_workflow_url} target="_blank" rel="noreferrer">打开调度定义</a>
          </span>,
          6,
        )
      }
      load()
    } catch (e: any) {
      message.error({ content: e?.response?.data?.detail || '同步失败', key: 'ds' })
    }
  }

  const handlePause = async (wf: any) => {
    try {
      const res: any = await workflowApi.pause(wf.id)
      message.success(res?.message || '已暂停调度')
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '暂停失败')
    }
  }

  const handleResume = async (wf: any) => {
    try {
      const res: any = await workflowApi.resume(wf.id)
      message.success(res?.message || '已恢复调度')
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '恢复失败')
    }
  }

  const handleOffline = async (wf: any) => {
    try {
      const res: any = await workflowApi.offline(wf.id)
      message.success(res?.message || '任务已下线')
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '下线失败')
    }
  }

  const submitPublishApproval = async () => {
    if (!approvalModal || !wsId) return
    try {
      await approvalApi.submit({
        workspace_id: wsId,
        resource_type: 'workflow',
        resource_id: approvalModal.id,
        action: 'publish_to_ds',
        submit_note: approvalNote || undefined,
      })
      message.success('已提交审批，请等待空间/平台管理员处理')
      setApprovalModal(null)
      setApprovalNote('')
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败')
    }
  }

  const isWorkflowPendingApproval = (wf: any) => pendingKeys.has(`workflow:${wf.id}:publish_to_ds`)

  const handleDelete = async (row: any) => {
    try {
      const res: any = await workflowApi.delete(row.id)
      message.success(res?.message || '删除成功')
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  const showInstances = async (wf: any) => {
    setSelectedWf(wf)
    const res: any = await workflowApi.instances(wf.id)
    setInstances(res as any[])
    setInstanceDrawer(true)
  }

  const handleRerun = async (instId: number) => {
    await workflowApi.rerun(selectedWf.id, instId)
    message.success('已提交重跑')
    const res: any = await workflowApi.instances(selectedWf.id)
    setInstances(res as any[])
  }

  const showNodeLog = (nodeInstances: any[]) => {
    setLogContent(nodeInstances)
    setLogModal(true)
  }

  const handleBatchRun = async () => {
    const values = await batchForm.validateFields()
    const [start, end] = values.date_range
    try {
      const res: any = await workflowApi.batchRun(
        selectedWf.id,
        start.format('YYYY-MM-DD'),
        end.format('YYYY-MM-DD')
      )
      message.success(res.message)
      setBatchModal(false)
      batchForm.resetFields()
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      message.error(typeof detail === 'string' ? detail : '补数据失败')
    }
  }

  const baseColumns = [
    { key: 'name', title: '工作流名称', dataIndex: 'name', width: 140, ellipsis: true },
    { key: 'description', title: '描述', dataIndex: 'description', width: 160, ellipsis: true },
    { key: 'version', title: '生产版本', dataIndex: 'active_version_no', width: 90, render: (v: number) => v ? <Tag color="blue">v{v}</Tag> : <Tag>草稿</Tag> },
    { key: 'created_by', title: '创建人', dataIndex: 'created_by_username', width: 88, render: (v: string) => v || '—' },
    { key: 'updated_by', title: '最近保存人', dataIndex: 'updated_by_username', width: 120, render: (v: string) => v || '—' },
    { key: 'node_count', title: '节点数', width: 72, align: 'center' as const, render: (_: any, row: any) => row.dag_config?.nodes?.length || 0 },
    {
      key: 'schedule',
      title: '调度',
      width: 120,
      render: (_: any, row: any) => (
        row.cron_expression
          ? <Tag color="purple">{row.cron_expression}</Tag>
          : <Tag>手动</Tag>
      )
    },
    {
      key: 'status',
      title: '生命周期',
      dataIndex: 'status',
      width: 110,
      render: (v: string, row: any) => {
        const status = v || (row.scheduler_definition_id ? 'published' : 'draft')
        const meta = WORKFLOW_STATUS[status] || WORKFLOW_STATUS.draft
        return (
          <Tooltip title={meta.desc}>
            <Tag color={meta.color}>{meta.label}</Tag>
          </Tooltip>
        )
      }
    },
    {
      key: 'scheduler',
      title: '发布状态',
      width: 120,
      render: (_: any, row: any) => (
        <Space size={4} wrap>
          {row.status === 'offline' ? <Tag color="red">已下线</Tag> : null}
          {row.status === 'paused' ? <Tag color="orange">调度暂停</Tag> : null}
          {row.needs_ds_republish && row.scheduler_definition_id ? (
            <Tag color="warning">待发布</Tag>
          ) : null}
          {isWorkflowPendingApproval(row) ? <Tag color="orange">审批中</Tag> : null}
          {row.dolphin_workflow_url ? (
            <a href={row.dolphin_workflow_url} target="_blank" rel="noreferrer">
              <LinkOutlined /> 打开
            </a>
          ) : (
            <span style={{ color: '#ccc' }}>—</span>
          )}
        </Space>
      ),
    },
    {
      key: 'actions',
      title: '操作',
      width: 420,
      render: (_: any, row: any) => (
        <Space>
          <Tooltip title="编辑"><Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)} /></Tooltip>
          <Tooltip title={row.status === 'offline' ? '已下线任务不能运行，请重新发布上线' : '立即运行'}>
            <Button
              size="small"
              type="primary"
              icon={<PlayCircleOutlined />}
              disabled={row.status === 'offline'}
              onClick={() => handleRun(row)}
            />
          </Tooltip>
          <Tooltip title={canPublishDirect ? '将当前定义与定时发布到生产调度' : '提交审批，由空间/平台管理员通过后发布到生产'}>
            <Button
              size="small"
              icon={<CloudUploadOutlined />}
              disabled={isWorkflowPendingApproval(row)}
              onClick={() => (canPublishDirect ? handlePublishToDS(row) : setApprovalModal(row))}
              style={{ color: row.scheduler_definition_id ? '#52c41a' : undefined }}
            >
              {isWorkflowPendingApproval(row)
                ? '审批中'
                : canPublishDirect
                  ? (row.status === 'offline' ? '重新上线' : row.scheduler_definition_id ? '发布变更' : '发布上线')
                  : '提交审批'}
            </Button>
          </Tooltip>
          {row.status === 'published' && row.schedule_type === 'cron' && (
            <Tooltip title="暂停周期调度；生产定义和历史实例保留，仍可手动运行/补数">
              <Button size="small" onClick={() => handlePause(row)}>暂停</Button>
            </Tooltip>
          )}
          {row.status === 'paused' && (
            <Tooltip title="恢复周期调度">
              <Button size="small" onClick={() => handleResume(row)}>恢复</Button>
            </Tooltip>
          )}
          {row.scheduler_definition_id && row.status !== 'offline' && (
            <Popconfirm
              title="下线任务？"
              description="将暂停周期并下线生产定义，历史实例保留；下线后不能运行或补数，重新发布可再次上线。"
              okText="下线"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={() => handleOffline(row)}
            >
              <Button size="small" danger>下线</Button>
            </Popconfirm>
          )}
          <Tooltip title="运行历史"><Button size="small" icon={<HistoryOutlined />} onClick={() => showInstances(row)} /></Tooltip>
          <Tooltip title={row.status === 'offline' ? '已下线任务不能补数，请重新发布上线' : '补数据'}>
            <Button
              size="small"
              icon={<CalendarOutlined />}
              disabled={row.status === 'offline'}
              onClick={() => { setSelectedWf(row); setBatchModal(true) }}
            />
          </Tooltip>
          <Popconfirm
            title="删除工作流？"
            description={
              row.scheduler_definition_id
                ? row.status === 'offline'
                  ? '将删除已下线的 GIDO 工作流记录，并尝试清理调度引擎定义。历史实例将不可通过该任务继续追溯。'
                  : '已上线工作流不能直接删除，请先执行「下线」以保留历史实例。'
                : '将删除平台工作流记录，不可恢复。'
            }
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            disabled={row.scheduler_definition_id && row.status !== 'offline'}
            onConfirm={() => handleDelete(row)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} disabled={row.scheduler_definition_id && row.status !== 'offline'} />
          </Popconfirm>
        </Space>
      )
    },
  ]

  const columns = useResizableTableColumns(baseColumns, {
    storageKey: wsId ? `gido.workflow.tableCols.w${wsId}` : undefined,
    defaultWidths: {
      name: 140,
      description: 160,
      created_by: 88,
      updated_by: 120,
      node_count: 72,
      schedule: 120,
      status: 110,
      scheduler: 120,
      actions: 420,
    },
  })

  const instColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '业务日期', dataIndex: 'business_date', width: 110 },
    { title: '提交人', dataIndex: 'submitted_by_username', width: 100, render: (v: string) => v || '—' },
    { title: '触发方式', dataIndex: 'trigger_type', width: 90, render: (t: string) => <Tag>{t}</Tag> },
    { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag> },
    {
      title: '开始时间',
      dataIndex: 'started_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '结束时间',
      dataIndex: 'finished_at',
      render: (v: string) => formatInTimeZone(v, displayTz),
    },
    {
      title: '操作', render: (_: any, row: any) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => showNodeLog(row.node_instances || [])}>节点日志</Button>
          {row.status === 'failed' && (
            <Button size="small" icon={<ReloadOutlined />} onClick={() => handleRerun(row.id)}>重跑</Button>
          )}
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: '#0f172a', letterSpacing: '-0.02em' }}>工作流</h2>
          <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>
            草稿在 GIDO 保存；发布上线后由调度引擎负责周期触发与执行；暂停只停周期，下线退出生产但保留历史实例。
          </div>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建工作流</Button>
      </div>

      <Table
        className="dw-resizable-table"
        dataSource={workflows}
        columns={columns}
        rowKey="id"
        tableLayout="fixed"
        scroll={{ x: 'max-content' }}
        size="middle"
      />

      {/* 新建/编辑工作流 */}
      <Modal
        title={editingWf ? `编辑工作流 - ${editingWf.name}` : '新建工作流'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        width={1080}
        styles={{ body: { maxHeight: 'calc(100vh - 160px)', overflowY: 'auto' } }}
        okText="保存"
      >
        <Tabs items={[
          {
            key: 'basic', label: '基本配置',
            children: (
              <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
                <Form.Item name="name" label="工作流名称" rules={[{ required: true }]}>
                  <Input />
                </Form.Item>
                <Form.Item name="description" label="描述">
                  <Input.TextArea rows={2} />
                </Form.Item>
                <Form.Item name="schedule_type" label="调度类型" initialValue="manual">
                  <Select
                    options={[
                      { label: '手动触发', value: 'manual' },
                      { label: 'Cron 定时', value: 'cron' }
                    ]}
                    onChange={v => {
                      setScheduleType(v)
                      if (v !== 'cron') {
                        form.setFieldValue('cron_expression', null)
                      }
                    }}
                  />
                </Form.Item>
                {scheduleType === 'cron' && (
                  <Form.Item
                    name="cron_expression"
                    label="调度时间"
                    rules={[
                      { required: true, message: '请配置 Cron 调度表达式' },
                      {
                        validator: async (_, v) => {
                          const parts = String(v || '').trim().split(/\s+/).filter(Boolean)
                          if (parts.length !== 5) {
                            throw new Error('Cron 须为 5 段：分 时 日 月 周，例如 0 2 * * *')
                          }
                        },
                      },
                    ]}
                  >
                    <CronBuilder />
                  </Form.Item>
                )}
              </Form>
            )
          },
          {
            key: 'dag', label: 'DAG 编排',
            children: (
              <div style={{ marginTop: 8 }}>
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="添加节点后拖拽排版，从端口可连多个上下游；双击节点可在弹窗中改配置（与数据开发同步）。DEPENDENT 可等待其他工作流成功。"
                />
                <DAGEditor
                  ref={dagEditorRef}
                  nodes={nodes}
                  value={dagConfig}
                  onChange={setDagConfig}
                  onNodeDoubleClick={(nodeId) => {
                    setNodeConfigId(nodeId)
                    setNodeConfigOpen(true)
                  }}
                />
              </div>
            )
          }
        ]} />
      </Modal>

      {wsId != null && (
        <NodeConfigModal
          open={nodeConfigOpen}
          nodeId={nodeConfigId}
          workspaceId={wsId}
          releaseOnClose
          onClose={() => {
            setNodeConfigOpen(false)
            setNodeConfigId(null)
          }}
          onSaved={(updated) => {
            setNodes(prev => prev.map(n => (n.id === updated.id ? { ...n, ...updated } : n)))
          }}
        />
      )}

      {/* 运行历史 */}
      <Drawer
        title={`运行历史 - ${selectedWf?.name}`}
        open={instanceDrawer}
        onClose={() => setInstanceDrawer(false)}
        width={900}
      >
        <Table dataSource={instances} columns={instColumns} rowKey="id" size="small" />
      </Drawer>

      {/* 节点日志 */}
      <Modal
        title="节点执行详情"
        open={logModal}
        onCancel={() => setLogModal(false)}
        footer={null}
        width={700}
      >
        {logContent.map((ni: any, idx: number) => (
          <div key={idx} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{ fontWeight: 600 }}>节点 {ni.node_id}</span>
              <Tag color={STATUS_COLOR[ni.status]}>{ni.status}</Tag>
            </div>
            <pre style={{
              background: '#1e1e1e', color: '#d4d4d4', padding: '8px 12px',
              borderRadius: 4, fontSize: 12, maxHeight: 150, overflow: 'auto',
              whiteSpace: 'pre-wrap', margin: 0
            }}>
              {ni.log || '暂无日志'}
            </pre>
          </div>
        ))}
        {logContent.length === 0 && <div style={{ color: '#bbb', textAlign: 'center', padding: 24 }}>暂无节点实例</div>}
      </Modal>

      {/* 补数据 */}
      <Modal
        title={`补数据 - ${selectedWf?.name}`}
        open={batchModal}
        onOk={handleBatchRun}
        onCancel={() => setBatchModal(false)}
      >
        <Form form={batchForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="date_range" label="业务日期范围" rules={[{ required: true }]}>
            <DatePicker.RangePicker style={{ width: '100%' }} />
          </Form.Item>
          <div style={{ color: '#666', fontSize: 12 }}>
            按业务日期逐天触发运行，最多 90 天。生产调度启用时每次会向调度引擎提交一次运行；未启用时由本地执行器消费队列。
          </div>
        </Form>
      </Modal>

      <Modal
        title={`提交发布审批 — ${approvalModal?.name || ''}`}
        open={!!approvalModal}
        onOk={submitPublishApproval}
        onCancel={() => { setApprovalModal(null); setApprovalNote('') }}
        okText="提交审批"
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="普通开发不能直接发布到生产。提交后由空间管理员或平台管理员审批，通过后将自动发布到生产调度。"
        />
        <Input.TextArea
          rows={3}
          placeholder="变更说明（可选）"
          value={approvalNote}
          onChange={e => setApprovalNote(e.target.value)}
        />
      </Modal>
    </div>
  )
}

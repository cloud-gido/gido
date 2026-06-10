/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect } from 'react'
import { Table, Button, Modal, Form, Input, Select, InputNumber, Tag, Space, message, Alert } from 'antd'
import { PlusOutlined, DeleteOutlined, ApiOutlined, EditOutlined } from '@ant-design/icons'
import { datasourceApi } from '../api'
import { useAppStore } from '../store'
import { isPlatformAdmin } from '../perm'

const DS_TYPES = ['mysql', 'postgresql', 'doris', 'hive', 'kafka', 'oss']
const JDBC_REQUIRES_DATABASE = ['mysql', 'postgresql', 'doris'] as const

export default function DatasourcePage() {
  const { currentWorkspace, user } = useAppStore()
  /** 与后端一致：当前空间管理员或平台管理员可在本空间新建/配置数据源（非仅用系统账号 is_admin） */
  const canManageWorkspaceDatasources = currentWorkspace?.my_role === 'admin' || isPlatformAdmin(user)
  const canCreateDatasource = canManageWorkspaceDatasources
  const canConfigureDatasource = canManageWorkspaceDatasources
  const wsId = currentWorkspace?.id
  const [list, setList] = useState<any[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [submitLoading, setSubmitLoading] = useState(false)
  const [form] = Form.useForm()

  const load = async () => {
    if (!wsId) return
    setList(await datasourceApi.list(wsId) as unknown as any[])
  }

  useEffect(() => { load() }, [wsId])

  const openCreate = () => {
    setEditingId(null)
    form.resetFields()
    setModalOpen(true)
  }

  const openEdit = async (row: any) => {
    try {
      const detail: any = await datasourceApi.get(row.id)
      setEditingId(row.id)
      form.setFieldsValue({
        name: detail.name,
        ds_type: detail.ds_type,
        host: detail.host,
        port: detail.port,
        database: detail.database,
        username: detail.username,
        password: undefined,
      })
      setModalOpen(true)
    } catch {
      message.error('加载数据源失败')
    }
  }

  /** 从 axios 错误拼可读文案（避免 null detail 时误用占位串挡住 e.message） */
  const buildSaveErrorMessage = (e: any): string => {
    if (e?.errorFields?.length) return ''
    const data = e?.response?.data
    const detail =
      data != null && typeof data === 'object' && 'detail' in data
        ? (data as { detail?: unknown }).detail
        : typeof data === 'string'
          ? data
          : undefined
    if (detail != null && detail !== '') {
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) {
        return detail
          .map((x: any) => (typeof x === 'object' && x?.msg != null ? String(x.msg) : JSON.stringify(x)))
          .join('；')
      }
      return String(detail)
    }
    if (e?.code === 'ECONNABORTED') return '请求超时'
    if (e?.message === 'Network Error') {
      return '网络异常：请确认 GIDO 后端已启动，且前端能通过 /api 访问（生产环境检查 Nginx 与 VITE_API_ORIGIN）'
    }
    if (e?.response?.status) return `请求失败（HTTP ${e.response.status}）`
    return e?.message || '保存失败'
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      if (!wsId) {
        message.warning('请先在工作空间切换器中选择工作空间')
        return
      }
      setSubmitLoading(true)
      if (editingId == null) {
        const created: any = await datasourceApi.create({ ...values, workspace_id: wsId })
        const ds = created?.dolphin_sync as string | undefined | null
        if (typeof ds === 'string' && ds.startsWith('error:')) {
          message.warning(`数据源已保存，但未成功同步 Dolphin：${ds.replace(/^error:/, '').trim()}`)
        } else if (ds === 'ok') {
          message.success('创建成功，已推送至 DolphinScheduler')
        } else {
          message.success('创建成功')
        }
      } else {
        const payload: Record<string, unknown> = { ...values }
        if (!payload.password) delete payload.password
        const saved: any = await datasourceApi.update(editingId, payload)
        const ds = saved?.dolphin_sync as string | undefined | null
        if (typeof ds === 'string' && ds.startsWith('error:')) {
          message.warning(`已保存本地配置，Dolphin 侧未同步：${ds.replace(/^error:/, '').trim()}`)
        } else if (ds === 'ok') {
          message.success('保存成功，已更新 DolphinScheduler 数据源')
        } else {
          message.success('保存成功')
        }
      }
      setModalOpen(false)
      setEditingId(null)
      form.resetFields()
      await load()
    } catch (e: any) {
      // 表单校验失败：Ant Design 已在字段上展示，不再弹全局错误
      if (e?.errorFields?.length) return
      const msg = buildSaveErrorMessage(e)
      if (msg) message.error(msg)
    } finally {
      setSubmitLoading(false)
    }
  }

  const handleDelete = async (id: number) => {
    await datasourceApi.delete(id)
    message.success('删除成功')
    load()
  }

  const handleTest = async (id: number) => {
    const res: any = await datasourceApi.test(id)
    if (res.status === 'success') message.success(res.message)
    else message.error(res.message)
  }

  const columns = [
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'ds_type', render: (t: string) => <Tag color="blue">{t}</Tag> },
    { title: 'Host', dataIndex: 'host' },
    { title: 'Port', dataIndex: 'port' },
    { title: '数据库', dataIndex: 'database' },
    { title: '用户名', dataIndex: 'username' },
    {
      title: '操作', render: (_: any, row: any) => (
        <Space>
          {canConfigureDatasource && (
            <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(row)}>编辑</Button>
          )}
          <Button size="small" icon={<ApiOutlined />} onClick={() => handleTest(row.id)}>测试连接</Button>
          {canConfigureDatasource && (
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(row.id)}>删除</Button>
          )}
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ marginBottom: 8 }}>数据源管理</h2>
          <div style={{ fontSize: 13, color: '#64748b' }}>
            数据源归属当前工作空间；DolphinScheduler 集成开启时对 mysql / postgresql / doris 会自动同步。
            <strong style={{ color: '#b45309' }}> JDBC 三类须填写数据库名</strong>
            ；mysql / postgresql 须填用户名。doris 可无用户（无认证 FE 常见），同步 Dolphin 时会自动用 <code>root</code> 作为 JDBC 用户名，避免 Dolphin 侧 <code>anonym@null</code>。
          </div>
        </div>
        {canCreateDatasource && (
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建数据源</Button>
        )}
      </div>
      {!canManageWorkspaceDatasources && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="新建与编辑数据源：需当前工作空间的「空间管理员」或平台管理员。其他成员可选用下列连接开发与集成。"
        />
      )}
      <Table dataSource={list} columns={columns} rowKey="id" />

      <Modal
        title={editingId == null ? '新建数据源' : '编辑数据源'}
        open={modalOpen}
        onOk={handleSubmit}
        confirmLoading={submitLoading}
        onCancel={() => { setModalOpen(false); setEditingId(null); form.resetFields() }}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="ds_type" label="类型" rules={[{ required: true }]}>
            <Select options={DS_TYPES.map(t => ({ label: t, value: t }))} />
          </Form.Item>
          <Form.Item name="host" label="Host"><Input /></Form.Item>
          <Form.Item name="port" label="Port"><InputNumber style={{ width: '100%' }} /></Form.Item>
          <Form.Item
            dependencies={['ds_type']}
            name="database"
            label="数据库"
            rules={[
              ({ getFieldValue }) => ({
                validator(_, val) {
                  const t = getFieldValue('ds_type')
                  if (!(JDBC_REQUIRES_DATABASE as readonly string[]).includes(t)) return Promise.resolve()
                  if (!val || !String(val).trim()) {
                    return Promise.reject(new Error('mysql / postgresql / doris 须填写数据库名（Dolphin 同步与 JDBC 必填）'))
                  }
                  return Promise.resolve()
                },
              }),
            ]}
            extra="Doris/MySQL/PG：填库名（如 Doris 默认库或业务库）；留空将无法同步 Dolphin。"
          >
            <Input placeholder="例如 default_catalog 下的库名（按实际 Doris/MySQL/PG）" />
          </Form.Item>
          <Form.Item
            name="username"
            label="用户名"
            dependencies={['ds_type']}
            rules={[
              ({ getFieldValue }) => ({
                validator(_, val) {
                  const t = getFieldValue('ds_type')
                  if (t !== 'mysql' && t !== 'postgresql') return Promise.resolve()
                  if (!val || !String(val).trim()) {
                    return Promise.reject(new Error('mysql / postgresql 须填写用户名'))
                  }
                  return Promise.resolve()
                },
              }),
            ]}
            extra="Doris：可不填（与 Studio 试跑一致）；同步 Dolphin 时以 root 占位。MySQL/PG：必填。"
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            extra={editingId != null ? '留空则不修改原密码' : undefined}
          >
            <Input.Password placeholder={editingId != null ? '不修改请留空' : undefined} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

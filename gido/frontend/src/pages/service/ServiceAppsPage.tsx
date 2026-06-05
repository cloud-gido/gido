/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState } from 'react'
import { Alert, Button, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { dataServiceApi } from '../../api'
import { useAppStore } from '../../store'
import { can, P } from '../../perm'
import { useServiceData, useWorkspaceId } from './ServiceContext'

const { TextArea } = Input
const { Text } = Typography

export default function ServiceAppsPage() {
  const wsId = useWorkspaceId()
  const { user, currentWorkspace } = useAppStore()
  const { apis, apps, loading, reload } = useServiceData()
  const canWrite = can(user, P.GIDO_SERVICE_WRITE, currentWorkspace)

  const [appModal, setAppModal] = useState(false)
  const [appForm] = Form.useForm()
  const [newAppSecret, setNewAppSecret] = useState<{ key: string; secret: string } | null>(null)
  const [grantModal, setGrantModal] = useState(false)
  const [grantApp, setGrantApp] = useState<any>(null)
  const [grantApiIds, setGrantApiIds] = useState<number[]>([])

  const createApp = async () => {
    const v = await appForm.validateFields()
    if (!wsId) return
    const ip_whitelist = v.ip_whitelist
      ? String(v.ip_whitelist).split(',').map((s: string) => s.trim()).filter(Boolean)
      : undefined
    const res: any = await dataServiceApi.createApp({
      workspace_id: wsId,
      name: v.name,
      description: v.description,
      qps_limit: v.qps_limit,
      ip_whitelist,
    })
    setNewAppSecret({ key: res.app_key, secret: res.app_secret })
    setAppModal(false)
    appForm.resetFields()
    reload()
  }

  if (!wsId) return <Alert type="info" message="请先选择工作空间" showIcon />

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>应用管理</h2>
          <Text type="secondary">管理消费者应用、AppKey / AppSecret 与 API 授权关系</Text>
        </div>
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { appForm.resetFields(); setAppModal(true) }}>
            新建应用
          </Button>
        )}
      </div>

      <Table
        dataSource={apps}
        rowKey="id"
        loading={loading}
        size="middle"
        columns={[
          { title: '应用名', dataIndex: 'name', width: 140 },
          { title: 'App Key', dataIndex: 'app_key', width: 160, render: (k: string) => <Text code>{k}</Text> },
          { title: 'QPS 限制', dataIndex: 'qps_limit', width: 90 },
          { title: '日配额', dataIndex: 'daily_quota', width: 90 },
          {
            title: '状态', dataIndex: 'is_active', width: 80,
            render: (v: boolean) => <Tag color={v ? 'success' : 'default'}>{v ? '启用' : '停用'}</Tag>,
          },
          {
            title: '已授权 API', dataIndex: 'granted_api_ids',
            render: (ids: number[]) => (ids || []).map(id => {
              const api = apis.find(a => a.id === id)
              return <Tag key={id}>{api?.name || id}</Tag>
            }),
          },
          {
            title: '操作', width: 160,
            render: (_: any, row: any) => canWrite ? (
              <Space>
                <Button size="small" onClick={() => { setGrantApp(row); setGrantApiIds(row.granted_api_ids || []); setGrantModal(true) }}>授权 API</Button>
                <Popconfirm title="删除应用？" onConfirm={async () => { await dataServiceApi.deleteApp(row.id); reload() }}>
                  <Button size="small" danger>删除</Button>
                </Popconfirm>
              </Space>
            ) : null,
          },
        ]}
      />

      <Modal title="新建消费者应用" open={appModal} onOk={createApp} onCancel={() => setAppModal(false)} okText="创建">
        <Form form={appForm} layout="vertical">
          <Form.Item name="name" label="应用名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><TextArea rows={2} /></Form.Item>
          <Form.Item name="qps_limit" label="QPS 限制" initialValue={100}><InputNumber min={0} style={{ width: '100%' }} /></Form.Item>
          <Form.Item name="ip_whitelist" label="IP 白名单（可选，逗号分隔）"><Input placeholder="10.0.0.0/8" /></Form.Item>
        </Form>
      </Modal>

      <Modal title="应用凭证（仅显示一次）" open={!!newAppSecret} onCancel={() => setNewAppSecret(null)}
        footer={[<Button key="ok" type="primary" onClick={() => setNewAppSecret(null)}>我已保存</Button>]}>
        {newAppSecret && (
          <Alert type="warning" showIcon message="请立即复制保存 AppSecret，关闭后无法再次查看" description={
            <div style={{ marginTop: 8 }}>
              <div>App Key: <Text code copyable>{newAppSecret.key}</Text></div>
              <div>App Secret: <Text code copyable>{newAppSecret.secret}</Text></div>
            </div>
          } />
        )}
      </Modal>

      <Modal title={`API 授权 - ${grantApp?.name}`} open={grantModal} onCancel={() => setGrantModal(false)}
        onOk={async () => {
          await dataServiceApi.grantApis(grantApp.id, { api_ids: grantApiIds })
          message.success('授权已更新')
          setGrantModal(false)
          reload()
        }} okText="保存授权">
        <Select mode="multiple" style={{ width: '100%' }} value={grantApiIds} onChange={setGrantApiIds}
          options={apis.map(a => ({ value: a.id, label: `${a.name} (${a.api_code})` }))} />
      </Modal>
    </div>
  )
}

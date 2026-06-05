/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState, useEffect } from 'react'
import { Table, Button, Modal, Form, Input, Select, Tag, Space, message, Row, Col, Statistic } from 'antd'
import { PlusOutlined, PlayCircleOutlined, DeleteOutlined } from '@ant-design/icons'
import { qualityApi, datamapApi } from '../api'
import { useAppStore } from '../store'

const RULE_TYPES = [
  { label: '完整性 completeness', value: 'completeness' },
  { label: '唯一性 uniqueness', value: 'uniqueness' },
  { label: '准确性 accuracy（自定义 SQL）', value: 'accuracy' },
  { label: '及时性 timeliness', value: 'timeliness' },
  { label: '自定义 SQL（兼容 Dolphin SQL 规则）', value: 'custom_sql' },
  { label: 'Dolphin SQL 镜像类型', value: 'dolphin_sql' },
  { label: '一致性 consistency', value: 'consistency' },
  { label: '有效性 validity', value: 'validity' },
]
const STATUS_COLOR: Record<string, string> = { pass: 'green', fail: 'red', warning: 'orange' }

export default function QualityPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const [rules, setRules] = useState<any[]>([])
  const [tables, setTables] = useState<any[]>([])
  const [dashboard, setDashboard] = useState<any>({})
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  const load = async () => {
    if (!wsId) return
    const [r, t, d]: any = await Promise.all([
      qualityApi.listRules(wsId),
      datamapApi.catalog(wsId),
      qualityApi.dashboard(wsId)
    ])
    setRules(r)
    setTables(t)
    setDashboard(d)
  }

  useEffect(() => { load() }, [wsId])

  const handleCreate = async () => {
    const values = await form.validateFields()
    values.workspace_id = wsId
    if (values.rule_config_json) {
      try {
        values.rule_config = JSON.parse(values.rule_config_json)
      } catch {
        message.error('规则配置须为合法 JSON')
        return
      }
      delete values.rule_config_json
    }
    if (values.dolphin_refs_json) {
      try {
        values.dolphin_refs = JSON.parse(values.dolphin_refs_json)
      } catch {
        message.error('Dolphin 联动配置须为合法 JSON')
        return
      }
      delete values.dolphin_refs_json
    }
    await qualityApi.createRule(values)
    message.success('创建成功')
    setModalOpen(false)
    load()
  }

  const handleCheck = async (id: number) => {
    const res: any = await qualityApi.runCheck(id)
    message.info(`检查完成: ${res.status} (得分: ${res.score})`)
    load()
  }

  const handleDelete = async (id: number) => {
    await qualityApi.deleteRule(id)
    message.success('删除成功')
    load()
  }

  const columns = [
    { title: '规则名称', dataIndex: 'rule_name' },
    { title: '规则类型', dataIndex: 'rule_type', render: (t: string) => <Tag color="purple">{t}</Tag> },
    {
      title: 'Dolphin 联动',
      width: 100,
      render: (_: any, row: any) => (row.dolphin_refs ? <Tag color="cyan">已配置</Tag> : <span style={{ color: '#999' }}>—</span>),
    },
    { title: '阈值', dataIndex: 'threshold' },
    { title: '状态', dataIndex: 'is_active', render: (v: boolean) => <Tag color={v ? 'green' : 'default'}>{v ? '启用' : '禁用'}</Tag> },
    {
      title: '操作', render: (_: any, row: any) => (
        <Space>
          <Button size="small" icon={<PlayCircleOutlined />} onClick={() => handleCheck(row.id)}>执行检查</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(row.id)}>删除</Button>
        </Space>
      )
    }
  ]

  return (
    <div>
      <h2>数据质量</h2>
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={4}><Statistic title="规则总数" value={dashboard.total_rules || 0} /></Col>
        <Col span={4}><Statistic title="通过" value={dashboard.pass || 0} valueStyle={{ color: '#52c41a' }} /></Col>
        <Col span={4}><Statistic title="失败" value={dashboard.fail || 0} valueStyle={{ color: '#ff4d4f' }} /></Col>
        <Col span={4}><Statistic title="告警" value={dashboard.warning || 0} valueStyle={{ color: '#faad14' }} /></Col>
        <Col span={4}><Statistic title="通过率" value={dashboard.pass_rate || 'N/A'} /></Col>
      </Row>

      <div style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { form.resetFields(); setModalOpen(true) }}>新建规则</Button>
        <span style={{ marginLeft: 12, color: '#666', fontSize: 13 }}>
          与 Dolphin 编排联动：在规则中填写 dolphin_refs（如 process_code / task_code），由 DS 工作流 HTTP/SQL 节点回调本平台的检查接口。
        </span>
      </div>

      <Table dataSource={rules} columns={columns} rowKey="id" />

      <Modal title="新建质量规则" open={modalOpen} onOk={handleCreate} onCancel={() => setModalOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="rule_name" label="规则名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="table_id" label="关联表（须已注册元数据）" rules={[{ required: true }]}>
            <Select
              showSearch
              optionFilterProp="label"
              options={tables.filter((t: any) => t.registered && t.meta_table_id).map((t: any) => ({
                label: t.qualified_name,
                value: t.meta_table_id,
              }))}
            />
          </Form.Item>
          <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
            <Select options={RULE_TYPES} />
          </Form.Item>
          <Form.Item name="threshold" label="阈值 (如 >=95)">
            <Input placeholder=">=95" />
          </Form.Item>
          <Form.Item
            name="rule_config_json"
            label="规则参数 JSON（可选）"
            tooltip={{ title: '例 completeness: {"column":"id"}；custom_sql: {"sql":"SELECT COUNT(*) FROM {table} WHERE …"}，其中 {table} 会替换为带库名的物理表' }}
          >
            <Input.TextArea rows={3} placeholder='{"column":"name"}' />
          </Form.Item>
          <Form.Item
            name="dolphin_refs_json"
            label="Dolphin 联动 JSON（可选）"
            tooltip={{ title: '存 DolphinScheduler 侧 process_code / task_code 等，便于与编排任务一一对应' }}
          >
            <Input.TextArea rows={2} placeholder='{"process_code":"","task_code":""}' />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

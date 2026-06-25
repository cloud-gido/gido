/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-24
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Form, Input, InputNumber, message, Select, Space, Switch, Table, Tag, Tooltip } from 'antd'
import { CheckCircleOutlined, FileTextOutlined, NotificationOutlined, ReloadOutlined, SettingOutlined } from '@ant-design/icons'
import { alertApi, operationApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'

const LEVEL_COLOR: Record<string, string> = {
  error: 'red',
  warning: 'orange',
  info: 'blue',
}

const STATUS_COLOR: Record<string, string> = {
  open: 'red',
  acknowledged: 'orange',
  resolved: 'green',
}

const NOTIFY_COLOR: Record<string, string> = {
  pending: 'orange',
  sent: 'green',
  partial: 'gold',
  failed: 'red',
  skipped: 'default',
}

export default function AlertCenterPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string>('open')
  const [loading, setLoading] = useState(false)
  const [logDrawer, setLogDrawer] = useState(false)
  const [logContent, setLogContent] = useState('')
  const [logHint, setLogHint] = useState('')
  const [configOpen, setConfigOpen] = useState(false)
  const [configLoading, setConfigLoading] = useState(false)
  const [configForm] = Form.useForm()

  const load = async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const res: any = await alertApi.list(wsId, { status: status === 'all' ? undefined : status, page, page_size: 20 })
      setRows(res.items || [])
      setTotal(res.total || 0)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [wsId, status, page])

  const ack = async (id: number) => {
    await alertApi.ack(id)
    message.success('已确认')
    load()
  }

  const resolve = async (id: number) => {
    await alertApi.resolve(id)
    message.success('已解决')
    load()
  }

  const sendNotify = async (id: number) => {
    const res: any = await alertApi.notify(id)
    if ((res.failed || []).length) {
      message.warning(`通知部分失败：${res.failed.map((x: any) => `${x.channel}: ${x.error}`).join('；')}`)
    } else {
      message.success('已发送通知')
    }
    load()
  }

  const openConfig = async () => {
    if (!wsId) return
    setConfigOpen(true)
    setConfigLoading(true)
    try {
      const cfg: any = await alertApi.getNotificationConfig(wsId)
      configForm.setFieldsValue(cfg)
    } finally {
      setConfigLoading(false)
    }
  }

  const saveConfig = async () => {
    if (!wsId) return
    const values = await configForm.validateFields()
    const cfg: any = await alertApi.putNotificationConfig(wsId, values)
    configForm.setFieldsValue(cfg)
    message.success('告警通知配置已保存')
  }

  const testConfig = async () => {
    if (!wsId) return
    const values = await configForm.validateFields()
    const res: any = await alertApi.testNotificationConfig(wsId, values)
    if ((res.failed || []).length) {
      message.warning(`测试部分失败：${res.failed.map((x: any) => `${x.channel}: ${x.error}`).join('；')}`)
    } else {
      message.success('测试通知已发送')
    }
  }

  const showLog = async (nodeInstanceId?: number) => {
    if (!nodeInstanceId) {
      message.info('这条告警没有关联节点实例日志')
      return
    }
    const res: any = await operationApi.getLog(nodeInstanceId)
    setLogContent(res.log || '暂无日志')
    setLogHint(res.log_source_hint || '')
    setLogDrawer(true)
  }

  const columns = [
    { title: '告警', dataIndex: 'id', width: 86, render: (id: number) => `#${id}` },
    { title: '级别', dataIndex: 'level', width: 90, render: (v: string) => <Tag color={LEVEL_COLOR[v] || 'default'}>{v}</Tag> },
    { title: '状态', dataIndex: 'status', width: 120, render: (v: string) => <Tag color={STATUS_COLOR[v] || 'default'}>{v}</Tag> },
    {
      title: '通知',
      dataIndex: 'notification_status',
      width: 100,
      render: (v: string) => <Tag color={NOTIFY_COLOR[v] || 'default'}>{v || 'pending'}</Tag>,
    },
    {
      title: '工作流 / 实例',
      width: 230,
      render: (_: any, row: any) => (
        <div>
          <div>{row.workflow_name || '-'}</div>
          <div style={{ color: '#888', fontSize: 12 }}>
            实例 #{row.workflow_instance_id || '-'}
            {row.business_date ? ` · 业务日期 ${row.business_date}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '失败节点',
      width: 210,
      render: (_: any, row: any) => (
        <div>
          <div>{row.node_name || (row.node_instance_id ? `节点实例 #${row.node_instance_id}` : '工作流级告警')}</div>
          <div style={{ color: '#888', fontSize: 12 }}>
            {row.node_type ? <Tag>{row.node_type}</Tag> : null}
            {row.node_instance_id ? `节点实例 #${row.node_instance_id}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '失败摘要',
      dataIndex: 'message',
      ellipsis: true,
      render: (messageText: string, row: any) => {
        const text = row.log_summary || messageText || ''
        return text ? <Tooltip title={text}><span>{text}</span></Tooltip> : '-'
      },
    },
    {
      title: '发生时间',
      dataIndex: 'occurred_at',
      width: 170,
      render: (v: string) => v ? formatInTimeZone(v, displayTz) : '-',
    },
    {
      title: '操作',
      width: 150,
      render: (_: any, row: any) => (
        <Space>
          <Button size="small" icon={<FileTextOutlined />} onClick={() => showLog(row.node_instance_id)}>日志</Button>
          <Button size="small" icon={<NotificationOutlined />} onClick={() => sendNotify(row.id)}>通知</Button>
          {row.status === 'open' && <Button size="small" onClick={() => ack(row.id)}>确认</Button>}
          {row.status !== 'resolved' && <Button size="small" type="primary" ghost onClick={() => resolve(row.id)}>解决</Button>}
        </Space>
      ),
    },
  ]

  return (
    <div>
      <h2>告警中心</h2>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="告警由 GIDO 实例和节点实例状态驱动"
        description="这里展示的是 GIDO 平台告警，不等同于调度引擎自身的 Alert 插件告警。发生时间优先取节点/实例结束时间；没有结束时间时才取告警入库时间。"
      />

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Select
          style={{ width: 160 }}
          value={status}
          onChange={(v) => { setStatus(v); setPage(1) }}
          options={[
            { value: 'open', label: '未处理' },
            { value: 'acknowledged', label: '已确认' },
            { value: 'resolved', label: '已解决' },
            { value: 'all', label: '全部' },
          ]}
        />
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        <Button icon={<CheckCircleOutlined />} onClick={() => { setStatus('open'); setPage(1) }}>查看未处理</Button>
        <Button icon={<SettingOutlined />} onClick={openConfig}>通知配置</Button>
      </div>

      <Table
        dataSource={rows}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{ total, pageSize: 20, current: page, onChange: setPage }}
      />
      <Drawer title="节点运行日志" open={logDrawer} onClose={() => setLogDrawer(false)} width={720}>
        {logHint && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={logHint} />}
        <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: 16, borderRadius: 4, minHeight: 420, whiteSpace: 'pre-wrap', fontSize: 13 }}>
          {logContent}
        </pre>
      </Drawer>
      <Drawer
        title="告警通知配置"
        open={configOpen}
        onClose={() => setConfigOpen(false)}
        width={720}
        extra={
          <Space>
            <Button onClick={testConfig} loading={configLoading}>测试发送</Button>
            <Button type="primary" onClick={saveConfig} loading={configLoading}>保存</Button>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="支持多渠道同时推送"
          description="Webhook 地址和 SMTP 密码只写不读；保存后页面仅显示脱敏值。飞书/Lark 与企业微信均使用机器人 Webhook。"
        />
        <Form form={configForm} layout="vertical" disabled={configLoading} initialValues={{ enabled: false, min_severity: 'error', smtp_port: 25 }}>
          <Form.Item name="enabled" label="启用通知" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="min_severity" label="最低推送级别">
            <Select
              options={[
                { value: 'info', label: 'info' },
                { value: 'warning', label: 'warning' },
                { value: 'error', label: 'error' },
                { value: 'critical', label: 'critical' },
              ]}
            />
          </Form.Item>

          <Form.Item name="email_enabled" label="邮件通知" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="email_to" label="收件人（逗号分隔）">
            <Input placeholder="ops@example.com, owner@example.com" />
          </Form.Item>
          <Space align="start" style={{ width: '100%' }}>
            <Form.Item name="smtp_host" label="SMTP Host" style={{ width: 260 }}>
              <Input placeholder="smtp.example.com" />
            </Form.Item>
            <Form.Item name="smtp_port" label="SMTP Port" style={{ width: 120 }}>
              <InputNumber min={1} max={65535} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="smtp_tls" label="TLS" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
          <Form.Item name="smtp_user" label="SMTP 用户">
            <Input />
          </Form.Item>
          <Form.Item name="smtp_password" label="SMTP 密码 / 授权码">
            <Input.Password placeholder="留空表示不修改已有密码" />
          </Form.Item>
          <Form.Item name="smtp_from" label="发件人">
            <Input placeholder="gido@example.com" />
          </Form.Item>

          <Form.Item name="webhook_enabled" label="通用 Webhook" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="webhook_url" label="通用 Webhook URL">
            <Input.Password placeholder="留空表示不修改已有地址；POST JSON {title, content, severity, alert_id}" />
          </Form.Item>

          <Form.Item name="lark_enabled" label="飞书 / Lark 机器人" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="lark_webhook_url" label="飞书 / Lark Webhook URL">
            <Input.Password placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
          </Form.Item>

          <Form.Item name="wecom_enabled" label="企业微信机器人" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item name="wecom_webhook_url" label="企业微信 Webhook URL">
            <Input.Password placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..." />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  )
}

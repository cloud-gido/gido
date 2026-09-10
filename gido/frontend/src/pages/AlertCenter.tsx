/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-24
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Drawer, Form, Input, InputNumber, message, Select, Space, Switch, Table, Tag, Tooltip } from 'antd'
import { CheckCircleOutlined, FileTextOutlined, NotificationOutlined, ReloadOutlined, SettingOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { alertApi, operationApi } from '../api'
import { useAppStore } from '../store'
import { formatInTimeZone } from '../utils/datetime'
import { R } from '../routes'
import { isPlatformAdmin } from '../perm'

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

const ALERT_TYPE_COLOR: Record<string, string> = {
  failed: 'red',
  recovered: 'green',
  test: 'blue',
}

export default function AlertCenterPage() {
  const { currentWorkspace, user, workspaces, setCurrentWorkspace } = useAppStore()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const platformAdmin = isPlatformAdmin(user)
  const [rows, setRows] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string>('open')
  const [keyword, setKeyword] = useState('')
  const [keywordDraft, setKeywordDraft] = useState('')
  const [notifyStatus, setNotifyStatus] = useState<string>('all')
  const [afterArmed, setAfterArmed] = useState(true)
  const [coverage, setCoverage] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [logDrawer, setLogDrawer] = useState(false)
  const [logContent, setLogContent] = useState('')
  const [logHint, setLogHint] = useState('')
  const [configOpen, setConfigOpen] = useState(false)
  const [configLoading, setConfigLoading] = useState(false)
  const [includeAllWorkspaces, setIncludeAllWorkspaces] = useState(false)
  const [configForm] = Form.useForm()
  const mutedUntil = Form.useWatch('muted_until', configForm)
  const notifyArmedAt = Form.useWatch('notify_armed_at', configForm)

  useEffect(() => {
    const urlWs = Number(searchParams.get('workspace_id') || 0)
    if (!urlWs || !Array.isArray(workspaces) || !workspaces.length) return
    const hit = workspaces.find((w: any) => Number(w.id) === urlWs)
    if (hit && Number(currentWorkspace?.id) !== urlWs) {
      setCurrentWorkspace(hit)
    }
  }, [searchParams, workspaces, currentWorkspace, setCurrentWorkspace])

  const load = async () => {
    if (!wsId) return
    setLoading(true)
    try {
      const res: any = await alertApi.list(wsId, {
        status: status === 'all' ? undefined : status,
        page,
        page_size: 20,
        include_all_workspaces: includeAllWorkspaces || undefined,
        q: keyword.trim() || undefined,
        notification_status: notifyStatus === 'all' ? undefined : notifyStatus,
        after_armed: afterArmed,
      })
      setRows(res.items || [])
      setTotal(res.total || 0)
      setCoverage(res.coverage || null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [wsId, status, page, includeAllWorkspaces, keyword, notifyStatus, afterArmed])

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
      configForm.setFieldsValue({ ...cfg, arm_from_now: true, mute_hours: undefined })
      if (cfg.coverage) setCoverage(cfg.coverage)
    } finally {
      setConfigLoading(false)
    }
  }

  const saveConfig = async () => {
    if (!wsId) return
    const values = await configForm.validateFields()
    const payload: Record<string, unknown> = { ...values }
    if (payload.mute_hours == null || payload.mute_hours === '') {
      delete payload.mute_hours
    }
    delete payload.muted_until
    delete payload.notify_armed_at
    const cfg: any = await alertApi.putNotificationConfig(wsId, payload)
    configForm.setFieldsValue({ ...cfg, mute_hours: undefined, arm_from_now: true })
    if (cfg.coverage) setCoverage(cfg.coverage)
    message.success('告警通知配置已保存；飞书只推送这一刻之后的失败')
  }

  const unmute = async () => {
    if (!wsId) return
    const cfg: any = await alertApi.putNotificationConfig(wsId, { mute_hours: 0 })
    configForm.setFieldsValue({ ...cfg, mute_hours: undefined })
    message.success('已解除静默')
  }

  const openInstance = (row: any) => {
    if (!row.workflow_instance_id) {
      message.info('这条告警没有关联工作流实例')
      return
    }
    const params = new URLSearchParams()
    if (row.workspace_id) params.set('workspace_id', String(row.workspace_id))
    params.set('instance', String(row.workflow_instance_id))
    navigate(`${R.batch.operation}?${params.toString()}`)
  }

  const testConfig = async () => {
    if (!wsId) return
    const values = await configForm.validateFields()
    const res: any = await alertApi.testNotificationConfig(wsId, values)
    if (res.coverage) setCoverage(res.coverage)
    if ((res.failed || []).length) {
      message.warning(`测试部分失败：${res.failed.map((x: any) => `${x.channel}: ${x.error}`).join('；')}`)
    } else {
      message.success('测试通知已发送')
    }
    const hints = res.coverage?.hints || []
    if (hints.length) {
      message.warning(hints[0])
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
    {
      title: '类型',
      dataIndex: 'alert_type',
      width: 90,
      render: (v: string) => <Tag color={ALERT_TYPE_COLOR[v] || 'default'}>{v || 'failed'}</Tag>,
    },
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
            {includeAllWorkspaces && row.workspace_name ? `${row.workspace_name} · ` : ''}
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
      width: 220,
      render: (_: any, row: any) => (
        <Space>
          <Button size="small" icon={<UnorderedListOutlined />} onClick={() => openInstance(row)}>实例</Button>
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
        message="工作流失败推一张飞书卡片，只报这一刻之后的"
        description="打开或保存通知配置后，历史失败只留在告警中心，不再刷群。节点失败不单独推送。卡片可跳回实例中心（平台集成 → 站点入口）。"
      />
      {(coverage?.hints || []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前空间可能配错或未配齐"
          description={
            <div>
              {(coverage.hints as string[]).map((h) => <div key={h}>{h}</div>)}
              {coverage.published_workflow_count > 0 && (
                <div style={{ marginTop: 8, color: '#666' }}>
                  已发布：{(coverage.published_workflow_names || []).join('、') || '—'}
                  {coverage.published_workflow_count > (coverage.published_workflow_names || []).length
                    ? ` 等 ${coverage.published_workflow_count} 条`
                    : ''}
                </div>
              )}
            </div>
          }
        />
      )}

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
        <Input.Search
          allowClear
          placeholder="工作流名称"
          style={{ width: 220 }}
          value={keywordDraft}
          onChange={(e) => setKeywordDraft(e.target.value)}
          onSearch={(v) => { setKeyword(v); setPage(1) }}
        />
        <Select
          style={{ width: 160 }}
          value={notifyStatus}
          onChange={(v) => { setNotifyStatus(v); setPage(1) }}
          options={[
            { value: 'all', label: '全部通知状态' },
            { value: 'sent', label: '已推送' },
            { value: 'partial', label: '部分推送' },
            { value: 'skipped', label: '未推送' },
            { value: 'failed', label: '推送失败' },
            { value: 'pending', label: '待推送' },
          ]}
        />
        <Tooltip title="默认隐藏推送起点之前、当时未推群的历史失败。打开后可检索这些入库记录。">
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Switch checked={!afterArmed} onChange={(v) => { setAfterArmed(!v); setPage(1) }} />
            <span style={{ color: '#666' }}>含历史入库</span>
          </span>
        </Tooltip>
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
        <Button icon={<CheckCircleOutlined />} onClick={() => { setStatus('open'); setPage(1) }}>查看未处理</Button>
        <Button icon={<SettingOutlined />} onClick={openConfig}>通知配置</Button>
        {platformAdmin && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Switch checked={includeAllWorkspaces} onChange={(v) => { setIncludeAllWorkspaces(v); setPage(1) }} />
            <span style={{ color: '#666' }}>全部工作空间</span>
          </span>
        )}
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
          description="Webhook 地址和 SMTP 密码只写不读。同一工作流失败默认 15 分钟内只推一张飞书卡片。保存配置默认从当前时刻起推送，不会把历史上已失败的实例再报一遍。"
        />
        {(coverage?.hints || []).length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="飞书可能推不到你以为的那条作业"
            description={(coverage.hints as string[]).join(' ')}
          />
        )}
        <Form
          form={configForm}
          layout="vertical"
          disabled={configLoading}
          initialValues={{ enabled: false, min_severity: 'error', smtp_port: 25, notify_cooldown_minutes: 15, arm_from_now: true }}
          onValuesChange={(changed, all) => {
            if (changed.lark_enabled === true && !all.enabled) {
              configForm.setFieldsValue({ enabled: true })
            }
          }}
        >
          <Form.Item name="enabled" label="启用通知" valuePropName="checked" extra="打开飞书/邮件等渠道后会自动打开；关掉对应渠道才会停推。">
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
          <Form.Item
            name="notify_cooldown_minutes"
            label="相同工作流冷却（分钟）"
            extra="同一工作流在冷却期内再次失败不会重复推飞书；恢复通知不受冷却限制。"
          >
            <InputNumber min={0} max={1440} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item
            name="arm_from_now"
            label="从当前时刻起推送"
            valuePropName="checked"
            extra={
              notifyArmedAt
                ? `当前推送起点 ${formatInTimeZone(notifyArmedAt, displayTz)}。打开此项并保存，会把起点改成现在，历史失败不再推群。`
                : "打开并保存后，只推送这一刻之后结束的失败；历史失败不补报。"
            }
          >
            <Switch />
          </Form.Item>
          <Form.Item name="notify_armed_at" hidden>
            <Input />
          </Form.Item>
          <Form.Item
            name="mute_hours"
            label="静默时长（小时）"
            extra={mutedUntil ? `当前静默至 ${formatInTimeZone(mutedUntil, displayTz)}` : '填写后保存即静默；留空表示不改当前静默状态。'}
          >
            <InputNumber min={0} max={168} placeholder="例如 2" style={{ width: 160 }} />
          </Form.Item>
          {mutedUntil ? (
            <Form.Item>
              <Button onClick={unmute}>解除静默</Button>
            </Form.Item>
          ) : null}
          <Form.Item name="muted_until" hidden>
            <Input />
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
            <Form.Item
              name="smtp_port"
              label="SMTP Port"
              style={{ width: 120 }}
              extra="465 自动 SSL；587/25 请开 TLS"
            >
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

          <Form.Item name="lark_enabled" label="飞书 / Lark 机器人" valuePropName="checked" extra="打开后，工作流失败会立刻推一张交互卡片（含失败节点）。">
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

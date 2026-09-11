/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-24
 */
import { useEffect, useState } from 'react'
import { Alert, Button, Divider, Dropdown, Drawer, Form, Input, InputNumber, message, Select, Space, Switch, Table, Tag, Tooltip, type MenuProps } from 'antd'
import { ClockCircleOutlined, DownOutlined, FileTextOutlined, NotificationOutlined, PlusOutlined, QuestionCircleOutlined, ReloadOutlined, SettingOutlined, TeamOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { alertApi, operationApi, workspaceApi } from '../api'
import RunCollectorStatus from '../components/RunCollectorStatus'
import RunDiagnosisDrawer, { type DiagnosisTarget } from '../components/RunDiagnosisDrawer'
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

const STATUS_LABEL: Record<string, string> = {
  open: '未处理',
  acknowledged: '已确认',
  resolved: '已解决',
}

const LEVEL_LABEL: Record<string, string> = {
  critical: '紧急',
  error: '严重',
  warning: '警告',
  info: '提示',
}

const NOTIFY_COLOR: Record<string, string> = {
  pending: 'orange',
  sent: 'green',
  partial: 'gold',
  failed: 'red',
  skipped: 'default',
  deferred: 'purple',
}

const NOTIFY_LABEL: Record<string, string> = {
  pending: '待推送',
  sent: '已推送',
  partial: '部分推送',
  failed: '推送失败',
  skipped: '未推送',
  deferred: '静默时段内暂缓',
}

const ALERT_TYPE_COLOR: Record<string, string> = {
  failed: 'red',
  recovered: 'green',
  sla: 'volcano',
  timeout: 'orange',
  test: 'blue',
}

const ALERT_TYPE_LABEL: Record<string, string> = {
  failed: '执行失败',
  recovered: '已恢复',
  sla: '未按时完成',
  timeout: '运行超时',
  test: '通道测试',
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
  const [collector, setCollector] = useState<any>(null)
  const [diagnosisTarget, setDiagnosisTarget] = useState<DiagnosisTarget | null>(null)
  const [oncallOpen, setOncallOpen] = useState(false)
  const [oncallLoading, setOncallLoading] = useState(false)
  const [oncall, setOncall] = useState<any>(null)
  const [members, setMembers] = useState<any[]>([])
  const [logDrawer, setLogDrawer] = useState(false)
  const [logContent, setLogContent] = useState('')
  const [logHint, setLogHint] = useState('')
  const [configOpen, setConfigOpen] = useState(false)
  const [configLoading, setConfigLoading] = useState(false)
  const [slaOpen, setSlaOpen] = useState(false)
  const [slaLoading, setSlaLoading] = useState(false)
  const [slaRows, setSlaRows] = useState<any[]>([])
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

  const loadAlerts = async () => {
    if (!wsId) return
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
    setCollector(res.collector || null)
  }

  const load = async () => {
    if (!wsId) return
    setLoading(true)
    try {
      await loadAlerts()
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [wsId, includeAllWorkspaces, status, page, keyword, notifyStatus, afterArmed])

  useEffect(() => {
    if (!wsId) return undefined
    const timer = window.setInterval(() => { loadAlerts() }, 15000)
    return () => window.clearInterval(timer)
  }, [wsId, includeAllWorkspaces, status, page, keyword, notifyStatus, afterArmed])

  const openSla = async () => {
    if (!wsId) return
    setSlaOpen(true)
    setSlaLoading(true)
    try {
      const res: any = await alertApi.listSlaRules(wsId)
      setSlaRows(res.items || [])
    } finally {
      setSlaLoading(false)
    }
  }

  /** 行内编辑先改本地，点保存才落库，避免每敲一个字符就发一次请求 */
  const patchSlaRow = (workflowId: number, patch: Record<string, unknown>) => {
    setSlaRows((prev) =>
      prev.map((row) =>
        row.workflow_id === workflowId
          ? { ...row, rule: { enabled: true, expect_finish_offset_days: 1, ...(row.rule || {}), ...patch } }
          : row,
      ),
    )
  }

  const saveSlaRow = async (row: any) => {
    if (!wsId) return
    try {
      const saved: any = await alertApi.putSlaRule(wsId, row.workflow_id, {
        enabled: row.rule?.enabled ?? true,
        expect_finish_time: row.rule?.expect_finish_time || '',
        expect_finish_offset_days: row.rule?.expect_finish_offset_days ?? 1,
        max_duration_minutes: row.rule?.max_duration_minutes ?? null,
        level: row.rule?.level || 'error',
      })
      setSlaRows((prev) =>
        prev.map((r) => (r.workflow_id === row.workflow_id ? { ...r, rule: saved } : r)),
      )
      message.success(`已保存「${row.workflow_name}」的基线`)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存基线失败')
    }
  }

  const removeSlaRow = async (row: any) => {
    if (!wsId) return
    await alertApi.deleteSlaRule(wsId, row.workflow_id)
    setSlaRows((prev) => prev.map((r) => (r.workflow_id === row.workflow_id ? { ...r, rule: null } : r)))
    message.success(`已清除「${row.workflow_name}」的基线`)
  }

  const openOncall = async () => {
    if (!wsId) return
    setOncallOpen(true)
    setOncallLoading(true)
    try {
      const [res, mem]: any[] = await Promise.all([
        alertApi.listOncallShifts(wsId),
        workspaceApi.members(wsId),
      ])
      setOncall(res)
      setMembers(Array.isArray(mem) ? mem : mem?.items || [])
    } finally {
      setOncallLoading(false)
    }
  }

  const saveShift = async (row: any) => {
    if (!wsId) return
    const body = {
      user_id: row.user_id,
      weekdays: row.weekdays || '*',
      start_time: row.start_time || '00:00',
      end_time: row.end_time || '24:00',
      enabled: row.enabled ?? true,
      note: row.note || '',
    }
    try {
      if (row.id) {
        await alertApi.updateOncallShift(wsId, row.id, body)
      } else {
        await alertApi.createOncallShift(wsId, body)
      }
      message.success('已保存值班班次')
      openOncall()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存值班班次失败')
    }
  }

  const removeShift = async (row: any) => {
    if (!wsId || !row.id) {
      // 还没落库的新行，直接从本地列表里去掉
      setOncall((prev: any) => ({ ...prev, items: (prev?.items || []).filter((r: any) => r !== row) }))
      return
    }
    await alertApi.deleteOncallShift(wsId, row.id)
    message.success('已删除值班班次')
    openOncall()
  }

  /** 行内编辑先改本地，点保存才落库 */
  const patchShift = (target: any, patch: Record<string, unknown>) => {
    setOncall((prev: any) => ({
      ...prev,
      items: (prev?.items || []).map((r: any) => (r === target ? { ...r, ...patch } : r)),
    }))
  }

  const addShiftRow = () => {
    setOncall((prev: any) => ({
      ...prev,
      items: [
        ...(prev?.items || []),
        { id: null, user_id: undefined, weekdays: '*', start_time: '09:00', end_time: '18:00', enabled: true },
      ],
    }))
  }

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

  const moreAlertActions = (row: any) => {
    const items: NonNullable<MenuProps['items']> = [
      {
        key: 'instance',
        icon: <UnorderedListOutlined />,
        label: '打开实例',
        disabled: !row.workflow_instance_id,
        onClick: () => openInstance(row),
      },
      {
        key: 'log',
        icon: <FileTextOutlined />,
        label: '节点日志',
        disabled: !row.node_instance_id,
        onClick: () => showLog(row.node_instance_id),
      },
      {
        key: 'notify',
        icon: <NotificationOutlined />,
        label: '重新推送通知',
        onClick: () => sendNotify(row.id),
      },
    ]
    // 已确认的还能直接解决；已解决的没有后续动作
    if (row.status === 'open') {
      items.push({ type: 'divider' })
      items.push({ key: 'resolve', label: '标记已解决', onClick: () => resolve(row.id) })
    }
    return items
  }

  const columns = [
    { title: '告警', dataIndex: 'id', width: 86, render: (id: number) => `#${id}` },
    {
      title: '类型',
      dataIndex: 'alert_type',
      width: 110,
      render: (v: string) => (
        <Tag color={ALERT_TYPE_COLOR[v] || 'default'}>{ALERT_TYPE_LABEL[v] || v || 'failed'}</Tag>
      ),
    },
    { title: '级别', dataIndex: 'level', width: 90, render: (v: string) => <Tag color={LEVEL_COLOR[v] || 'default'}>{LEVEL_LABEL[v] || v}</Tag> },
    { title: '状态', dataIndex: 'status', width: 100, render: (v: string) => <Tag color={STATUS_COLOR[v] || 'default'}>{STATUS_LABEL[v] || v}</Tag> },
    {
      title: '通知',
      dataIndex: 'notification_status',
      width: 150,
      render: (v: string, row: any) => {
        const tag = <Tag color={NOTIFY_COLOR[v] || 'default'}>{NOTIFY_LABEL[v] || v || 'pending'}</Tag>
        if (v === 'deferred') {
          return (
            <Tooltip
              title={
                row.notify_next_retry_at
                  ? `静默时段内不推群，${formatInTimeZone(row.notify_next_retry_at, displayTz)} 自动补推`
                  : '静默时段内不推群，时段结束后自动补推'
              }
            >
              {tag}
            </Tooltip>
          )
        }
        if (v !== 'failed' && v !== 'partial') return tag
        const tip = [
          row.notify_pending_channels ? `待重投渠道：${row.notify_pending_channels}` : null,
          row.notify_next_retry_at
            ? `下次重投：${formatInTimeZone(row.notify_next_retry_at, displayTz)}`
            : '已达重试上限，需人工处理',
          row.notify_last_error ? `最近错误：${row.notify_last_error}` : null,
        ]
          .filter(Boolean)
          .join('\n')
        return (
          <Tooltip title={tip}>
            <span>
              {tag}
              {row.notify_attempts ? (
                <span style={{ color: '#888', fontSize: 12 }}>×{row.notify_attempts}</span>
              ) : null}
            </span>
          </Tooltip>
        )
      },
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
      width: 200,
      render: (_: any, row: any) => (
        <Space size={4}>
          {row.workflow_id && (
            <Tooltip title="为什么这次没跑 / 还没跑完">
              <Button
                type="link"
                size="small"
                icon={<QuestionCircleOutlined />}
                onClick={() => setDiagnosisTarget({
                  workspaceId: row.workspace_id || wsId!,
                  workflowId: row.workflow_id,
                  workflowName: row.workflow_name,
                  businessDate: row.business_date,
                })}
              >
                诊断
              </Button>
            </Tooltip>
          )}
          {/* 未处理先确认（认领），确认过就只剩解决 */}
          {row.status === 'open' ? (
            <Button type="link" size="small" onClick={() => ack(row.id)}>确认</Button>
          ) : row.status !== 'resolved' ? (
            <Button type="link" size="small" onClick={() => resolve(row.id)}>解决</Button>
          ) : null}
          <Dropdown trigger={['click']} menu={{ items: moreAlertActions(row) }}>
            <Button type="link" size="small">更多 <DownOutlined /></Button>
          </Dropdown>
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
        message="执行失败、未按时完成、运行超时都会进入本页并按值班配置推送"
        description="失败由运行采集实时发现；未按时完成与运行超时由「基线」按分钟巡检。打开或保存通知配置后，历史失败只留在告警中心，不再刷群。节点失败不单独推送。"
      />
      <RunCollectorStatus collector={collector} />
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
        {/* 三个入口都是「配置怎么告警」，收进一个菜单，工具栏留给筛选 */}
        <Dropdown
          trigger={['click']}
          menu={{
            items: [
              { key: 'notify', icon: <SettingOutlined />, label: '通知配置（渠道 / 静默时段）', onClick: openConfig },
              { key: 'sla', icon: <ClockCircleOutlined />, label: '基线（承诺完成时间 / 最长时长）', onClick: openSla },
              { key: 'oncall', icon: <TeamOutlined />, label: '值班表', onClick: openOncall },
            ],
          }}
        >
          <Button icon={<SettingOutlined />}>告警设置 <DownOutlined /></Button>
        </Dropdown>
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
      <RunDiagnosisDrawer target={diagnosisTarget} onClose={() => setDiagnosisTarget(null)} />
      <Drawer title="节点运行日志" open={logDrawer} onClose={() => setLogDrawer(false)} width={720}>
        {logHint && <Alert type="info" showIcon style={{ marginBottom: 12 }} message={logHint} />}
        <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: 16, borderRadius: 4, minHeight: 420, whiteSpace: 'pre-wrap', fontSize: 13 }}>
          {logContent}
        </pre>
      </Drawer>
      <Drawer
        title="值班表"
        open={oncallOpen}
        onClose={() => setOncallOpen(false)}
        width={860}
        extra={<Button icon={<PlusOutlined />} onClick={addShiftRow}>添加班次</Button>}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={`当前值班：${oncall?.on_call_now || '未排班'}${oncall?.local_now ? ` · 空间本地时间 ${oncall.local_now}` : ''}`}
          description="新告警会自动落到当班人名下，飞书卡片里也会点名，群里不用再问「这个谁看」。时段支持跨零点（如 22:00 – 06:00），按工作空间时区判断。"
        />
        <Table
          rowKey={(row: any) => row.id ?? `new-${(oncall?.items || []).indexOf(row)}`}
          size="small"
          loading={oncallLoading}
          dataSource={oncall?.items || []}
          pagination={false}
          locale={{ emptyText: '还没有排班。没有值班表时告警不会自动指派。' }}
          columns={[
            {
              title: '值班人',
              width: 180,
              render: (_: any, row: any) => (
                <Select
                  style={{ width: 160 }}
                  placeholder="选择成员"
                  value={row.user_id}
                  onChange={(v) => patchShift(row, { user_id: v })}
                  options={members.map((m: any) => ({
                    value: m.user_id ?? m.id,
                    label: m.username || m.name || `用户 #${m.user_id ?? m.id}`,
                  }))}
                />
              ),
            },
            {
              title: '星期',
              width: 220,
              render: (_: any, row: any) => (
                <Select
                  mode="multiple"
                  allowClear
                  style={{ width: 200 }}
                  placeholder="每天"
                  value={row.weekdays === '*' || !row.weekdays ? [] : String(row.weekdays).split(',')}
                  onChange={(v: string[]) => patchShift(row, { weekdays: v.length ? v.join(',') : '*' })}
                  options={[
                    { value: '1', label: '周一' },
                    { value: '2', label: '周二' },
                    { value: '3', label: '周三' },
                    { value: '4', label: '周四' },
                    { value: '5', label: '周五' },
                    { value: '6', label: '周六' },
                    { value: '7', label: '周日' },
                  ]}
                />
              ),
            },
            {
              title: '时段',
              width: 200,
              render: (_: any, row: any) => (
                <Space size={4}>
                  <Input
                    style={{ width: 80 }}
                    placeholder="09:00"
                    value={row.start_time || ''}
                    onChange={(e) => patchShift(row, { start_time: e.target.value })}
                  />
                  <span>–</span>
                  <Input
                    style={{ width: 80 }}
                    placeholder="18:00"
                    value={row.end_time || ''}
                    onChange={(e) => patchShift(row, { end_time: e.target.value })}
                  />
                </Space>
              ),
            },
            {
              title: '启用',
              width: 90,
              render: (_: any, row: any) => (
                <Space size={4}>
                  <Switch
                    size="small"
                    checked={row.enabled ?? true}
                    onChange={(v) => patchShift(row, { enabled: v })}
                  />
                  {row.on_call_now ? <Tag color="green">在班</Tag> : null}
                </Space>
              ),
            },
            {
              title: '操作',
              width: 130,
              render: (_: any, row: any) => (
                <Space>
                  <Button size="small" type="link" onClick={() => saveShift(row)}>保存</Button>
                  <Button size="small" type="link" danger onClick={() => removeShift(row)}>删除</Button>
                </Space>
              ),
            },
          ]}
        />
      </Drawer>
      <Drawer
        title="基线（承诺完成时间 / 最长运行时长）"
        open={slaOpen}
        onClose={() => setSlaOpen(false)}
        width={860}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="只有失败告警是不够的"
          description="基线负责另外两类事故：到了承诺时间还没出数据（哪怕压根没跑起来），以及任务还在跑但已经远超正常时长。GIDO 每分钟巡检一次，破线写入告警中心并按值班配置推送；补跑成功后告警会自动关闭。"
        />
        <Table
          rowKey="workflow_id"
          size="small"
          loading={slaLoading}
          dataSource={slaRows}
          pagination={false}
          columns={[
            { title: '工作流', dataIndex: 'workflow_name', ellipsis: true },
            {
              title: '承诺完成时间',
              width: 200,
              render: (_: any, row: any) => (
                <Space>
                  <Input
                    style={{ width: 88 }}
                    placeholder="09:30"
                    value={row.rule?.expect_finish_time || ''}
                    onChange={(e) => patchSlaRow(row.workflow_id, { expect_finish_time: e.target.value })}
                  />
                  <Select
                    style={{ width: 96 }}
                    value={row.rule?.expect_finish_offset_days ?? 1}
                    onChange={(v) => patchSlaRow(row.workflow_id, { expect_finish_offset_days: v })}
                    options={[
                      { value: 0, label: '当天' },
                      { value: 1, label: 'T+1' },
                      { value: 2, label: 'T+2' },
                    ]}
                  />
                </Space>
              ),
            },
            {
              title: '最长运行时长',
              width: 150,
              render: (_: any, row: any) => (
                <InputNumber
                  style={{ width: 120 }}
                  min={1}
                  addonAfter="分"
                  value={row.rule?.max_duration_minutes ?? undefined}
                  onChange={(v) => patchSlaRow(row.workflow_id, { max_duration_minutes: v })}
                />
              ),
            },
            {
              title: '启用',
              width: 80,
              render: (_: any, row: any) => (
                <Switch
                  size="small"
                  checked={row.rule?.enabled ?? true}
                  onChange={(v) => patchSlaRow(row.workflow_id, { enabled: v })}
                />
              ),
            },
            {
              title: '操作',
              width: 140,
              render: (_: any, row: any) => (
                <Space>
                  <Button size="small" type="link" onClick={() => saveSlaRow(row)}>保存</Button>
                  {row.rule && (
                    <Button size="small" type="link" danger onClick={() => removeSlaRow(row)}>清除</Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
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
          initialValues={{ enabled: false, min_severity: 'error', smtp_port: 25, notify_cooldown_minutes: 15, arm_from_now: true, quiet_hours_min_severity: 'critical' }}
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

          <Divider orientation="left" plain>静默时段（值班作息）</Divider>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="夜里只放过够严重的，其余压到时段结束再推"
            description="被压住的告警照样进告警中心，只是不在时段内推群；时段一结束由后台自动补推，不会丢。时段支持跨零点，按工作空间时区判断。"
          />
          <Form.Item name="quiet_hours_enabled" label="启用静默时段" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space size={8} style={{ display: 'flex', marginBottom: 8 }}>
            <Form.Item name="quiet_hours_start" label="开始" style={{ marginBottom: 0 }}>
              <Input placeholder="23:00" style={{ width: 120 }} />
            </Form.Item>
            <Form.Item name="quiet_hours_end" label="结束" style={{ marginBottom: 0 }}>
              <Input placeholder="08:00" style={{ width: 120 }} />
            </Form.Item>
            <Form.Item
              name="quiet_hours_min_severity"
              label="时段内仍立即推送的级别"
              style={{ marginBottom: 0 }}
            >
              <Select
                style={{ width: 160 }}
                options={[
                  { value: 'warning', label: 'warning 及以上' },
                  { value: 'error', label: 'error 及以上' },
                  { value: 'critical', label: '仅 critical' },
                ]}
              />
            </Form.Item>
          </Space>

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

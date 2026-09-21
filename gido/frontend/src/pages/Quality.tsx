/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Table, Button, Modal, Form, Input, Select, Tag, Space, message, Row, Col, Statistic,
  Drawer, Switch, Radio, InputNumber, Alert, Typography, AutoComplete, DatePicker,
} from 'antd'
import {
  PlusOutlined, PlayCircleOutlined, DeleteOutlined, EditOutlined, HistoryOutlined,
} from '@ant-design/icons'
import dayjs from 'dayjs'
import { qualityApi, datamapApi } from '../api'
import { useAppStore } from '../store'

const RULE_TYPES = [
  { label: '完整性', value: 'completeness' },
  { label: '唯一性', value: 'uniqueness' },
  { label: '有效性', value: 'validity' },
  { label: '及时性', value: 'timeliness' },
  { label: '一致性（左右 SQL 对比）', value: 'consistency' },
  { label: '准确性（自定义 SQL 得分）', value: 'accuracy' },
  { label: '自定义 SQL', value: 'custom_sql' },
  { label: 'Dolphin SQL 镜像', value: 'dolphin_sql' },
]

const CRON_PRESETS = [
  { label: '不调度（仅手动）', value: '' },
  { label: '每天 02:00', value: '0 2 * * *' },
  { label: '每天 06:00', value: '0 6 * * *' },
  { label: '每小时', value: '0 * * * *' },
]

const RULE_TYPE_LABEL: Record<string, string> = Object.fromEntries(RULE_TYPES.map((item) => [item.value, item.label]))
const STATUS_COLOR: Record<string, string> = { pass: 'green', fail: 'red', warning: 'orange' }
const NEEDS_COLUMN = new Set(['completeness', 'uniqueness', 'validity', 'timeliness'])

type RuleRow = {
  id: number
  rule_name: string
  rule_type: string
  table_id: number
  qualified_name?: string
  threshold?: string
  severity?: string
  schedule_cron?: string
  is_active?: boolean
  rule_config?: Record<string, unknown>
  dolphin_refs?: Record<string, unknown>
  latest_status?: string
  latest_score?: number
  latest_checked_at?: string
}

export default function QualityPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id
  const [searchParams, setSearchParams] = useSearchParams()
  const [rules, setRules] = useState<RuleRow[]>([])
  const [tables, setTables] = useState<any[]>([])
  const [dashboard, setDashboard] = useState<any>({})
  const [workspaceTrend, setWorkspaceTrend] = useState<any[]>([])
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<RuleRow | null>(null)
  const [columns, setColumns] = useState<string[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [historyRule, setHistoryRule] = useState<RuleRow | null>(null)
  const [historyRows, setHistoryRows] = useState<any[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [sampleRows, setSampleRows] = useState<any[]>([])
  const [saving, setSaving] = useState(false)
  const [checkBizdate, setCheckBizdate] = useState<string>(dayjs().subtract(1, 'day').format('YYYY-MM-DD'))
  const [form] = Form.useForm()
  const ruleType = Form.useWatch('rule_type', form)
  const validityMode = Form.useWatch('validity_mode', form)
  const tableKey = Form.useWatch('table_key', form)

  const load = useCallback(async () => {
    if (!wsId) return
    const [r, t, d, trend]: any = await Promise.all([
      qualityApi.listRules(wsId),
      datamapApi.catalog(wsId),
      qualityApi.dashboard(wsId),
      qualityApi.workspaceTrend(wsId, 7).catch(() => []),
    ])
    setRules(Array.isArray(r) ? r : [])
    setTables(Array.isArray(t) ? t : [])
    setDashboard(d || {})
    setWorkspaceTrend(Array.isArray(trend) ? trend : [])
  }, [wsId])

  useEffect(() => { void load() }, [load])

  const openHistory = useCallback(async (row: RuleRow) => {
    setHistoryRule(row)
    setHistoryOpen(true)
    setHistoryLoading(true)
    setSampleRows([])
    try {
      const rows: any = await qualityApi.records(row.id)
      const list = Array.isArray(rows) ? rows : []
      setHistoryRows(list)
      const latestSample = list.find((item: any) => Array.isArray(item?.detail?.sample_rows) && item.detail.sample_rows.length)
      setSampleRows(latestSample?.detail?.sample_rows || [])
    } catch {
      setHistoryRows([])
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  const loadColumnsForTable = async (metaTableId?: number) => {
    if (!metaTableId) {
      setColumns([])
      return
    }
    try {
      const detail: any = await datamapApi.getTable(metaTableId)
      const names = (detail?.columns || [])
        .map((col: any) => col.name || col.column_name)
        .filter(Boolean)
      setColumns(names)
    } catch {
      setColumns([])
    }
  }

  const openCreate = useCallback((preset?: { table_id?: number; table_key?: string }) => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({
      rule_type: 'completeness',
      threshold: '>=95',
      severity: 'warn',
      validity_mode: 'regex',
      max_delay_hours: 24,
      schedule_cron: '',
      tolerance_pct: 0,
      table_key: preset?.table_key,
    })
    setColumns([])
    if (preset?.table_id) void loadColumnsForTable(preset.table_id)
    setModalOpen(true)
  }, [form])

  useEffect(() => {
    const ruleId = searchParams.get('rule_id')
    const tableId = searchParams.get('table_id')
    if (ruleId && rules.length) {
      const hit = rules.find((row) => Number(row.id) === Number(ruleId))
      if (hit) {
        void openHistory(hit)
        const next = new URLSearchParams(searchParams)
        next.delete('rule_id')
        setSearchParams(next, { replace: true })
      }
      return
    }
    if (tableId && tables.length && !modalOpen) {
      const row = tables.find((t: any) => Number(t.meta_table_id) === Number(tableId))
      openCreate({ table_id: Number(tableId), table_key: row?.row_key })
      const next = new URLSearchParams(searchParams)
      next.delete('table_id')
      setSearchParams(next, { replace: true })
    }
  }, [rules, tables, searchParams, setSearchParams, openHistory, openCreate, modalOpen])

  useEffect(() => {
    if (!tableKey || editing) return
    const row = tables.find((t: any) => t.row_key === tableKey)
    void loadColumnsForTable(row?.meta_table_id)
  }, [tableKey, tables, editing])

  const buildRuleConfig = (values: any) => {
    const type = values.rule_type
    const base: Record<string, unknown> = {}
    if (values.partition_column) {
      base.partition_column = values.partition_column
      base.partition_value = values.partition_value
    }
    if (type === 'completeness' || type === 'uniqueness') {
      return { ...base, column: values.column }
    }
    if (type === 'timeliness') {
      return {
        ...base,
        time_column: values.time_column || values.column,
        max_delay_hours: values.max_delay_hours ?? 24,
      }
    }
    if (type === 'validity') {
      const mode = values.validity_mode || 'regex'
      if (mode === 'regex') return { ...base, mode, column: values.column, pattern: values.pattern }
      if (mode === 'enum') {
        return {
          ...base,
          mode,
          column: values.column,
          allowed_values: Array.isArray(values.allowed_values) ? values.allowed_values : [],
        }
      }
      return {
        ...base,
        mode: 'range',
        column: values.column,
        min_value: values.min_value,
        max_value: values.max_value,
      }
    }
    if (type === 'consistency') {
      return {
        ...base,
        left_sql: values.left_sql,
        right_sql: values.right_sql,
        tolerance_pct: values.tolerance_pct ?? 0,
      }
    }
    if (type === 'accuracy' || type === 'custom_sql' || type === 'dolphin_sql') {
      return { ...base, sql: values.sql }
    }
    return base
  }

  const openEdit = async (row: RuleRow) => {
    setEditing(row)
    const cfg = row.rule_config || {}
    form.setFieldsValue({
      rule_name: row.rule_name,
      rule_type: row.rule_type,
      threshold: row.threshold || '>=95',
      severity: row.severity === 'block' ? 'block' : 'warn',
      schedule_cron: row.schedule_cron || '',
      column: cfg.column || cfg.time_column,
      time_column: cfg.time_column || cfg.column,
      max_delay_hours: cfg.max_delay_hours ?? 24,
      validity_mode: cfg.mode || (cfg.pattern ? 'regex' : cfg.allowed_values ? 'enum' : cfg.min_value != null || cfg.max_value != null ? 'range' : 'regex'),
      pattern: cfg.pattern,
      allowed_values: cfg.allowed_values,
      min_value: cfg.min_value,
      max_value: cfg.max_value,
      sql: cfg.sql,
      left_sql: cfg.left_sql || cfg.sql_a,
      right_sql: cfg.right_sql || cfg.sql_b,
      tolerance_pct: cfg.tolerance_pct ?? 0,
      partition_column: cfg.partition_column,
      partition_value: cfg.partition_value,
      dolphin_refs_json: row.dolphin_refs ? JSON.stringify(row.dolphin_refs, null, 2) : undefined,
    })
    await loadColumnsForTable(row.table_id)
    setModalOpen(true)
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    if (!wsId) return
    setSaving(true)
    try {
      let dolphin_refs: Record<string, unknown> | undefined
      if (values.dolphin_refs_json) {
        try {
          dolphin_refs = JSON.parse(values.dolphin_refs_json)
        } catch {
          message.error('Dolphin 联动配置须为合法 JSON')
          return
        }
      }
      const rule_config = buildRuleConfig(values)
      const schedule_cron = values.schedule_cron || null
      if (editing) {
        await qualityApi.updateRule(editing.id, {
          rule_name: values.rule_name,
          rule_type: values.rule_type,
          threshold: values.threshold,
          severity: values.severity,
          schedule_cron,
          rule_config,
          dolphin_refs: dolphin_refs ?? null,
        })
        message.success('已保存')
      } else {
        const row = tables.find((t: any) => t.row_key === values.table_key)
        if (!row?.table_name) {
          message.error('请选择表')
          return
        }
        let tableId = row.meta_table_id
        if (!tableId) {
          const ensured: any = await datamapApi.ensureTable({
            workspace_id: wsId,
            datasource_id: row.datasource_id,
            db_name: row.catalog || undefined,
            table_name: row.table_name,
            table_comment: row.table_comment || undefined,
            table_type: row.table_type || 'table',
            sync_if_empty: true,
          })
          tableId = Number(ensured?.id) || undefined
        }
        if (!tableId) {
          message.error('无法关联该表')
          return
        }
        await qualityApi.createRule({
          workspace_id: wsId,
          table_id: tableId,
          rule_name: values.rule_name,
          rule_type: values.rule_type,
          threshold: values.threshold,
          severity: values.severity,
          schedule_cron,
          rule_config,
          dolphin_refs,
        })
        message.success('创建成功')
      }
      setModalOpen(false)
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleCheck = async (id: number) => {
    try {
      const res: any = await qualityApi.runCheck(id, { bizdate: checkBizdate })
      const blockNote = res.blocking ? '（强规则，可阻断下游）' : ''
      const sampleNote = Array.isArray(res?.detail?.sample_rows) && res.detail.sample_rows.length
        ? ` · 抽样 ${res.detail.sample_rows.length} 行`
        : ''
      if (res.status === 'fail') {
        message.error(`检查失败: ${res.score}${blockNote}${sampleNote}`)
      } else if (res.status === 'warning') {
        message.warning(`检查告警: ${res.score}`)
      } else {
        message.success(`检查通过: ${res.score}`)
      }
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '检查失败')
    }
  }

  const handleToggleActive = async (row: RuleRow, checked: boolean) => {
    try {
      await qualityApi.updateRule(row.id, { is_active: checked })
      message.success(checked ? '已启用' : '已停用')
      await load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新失败')
    }
  }

  const handleDelete = async (id: number) => {
    Modal.confirm({
      title: '删除这条质量规则？',
      content: '检查历史会一并删除。也可先停用，保留配置。',
      okType: 'danger',
      onOk: async () => {
        await qualityApi.deleteRule(id)
        message.success('删除成功')
        await load()
      },
    })
  }

  const columnOptions = useMemo(
    () => columns.map((name) => ({ label: name, value: name })),
    [columns],
  )

  const trendHint = useMemo(() => {
    if (!workspaceTrend.length) return null
    const failSum = workspaceTrend.reduce((acc, day) => acc + (day.fail || 0), 0)
    const passSum = workspaceTrend.reduce((acc, day) => acc + (day.pass || 0), 0)
    return `近 7 天检查 ${passSum + failSum} 次 · 失败 ${failSum} 次 · 定时规则 ${dashboard.scheduled_rules || 0} 条`
  }, [workspaceTrend, dashboard.scheduled_rules])

  const columnsDef = [
    { title: '规则名称', dataIndex: 'rule_name', ellipsis: true },
    {
      title: '表',
      dataIndex: 'qualified_name',
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: '类型',
      dataIndex: 'rule_type',
      width: 110,
      render: (t: string) => <Tag>{RULE_TYPE_LABEL[t] || t}</Tag>,
    },
    {
      title: '强弱',
      dataIndex: 'severity',
      width: 72,
      render: (v: string) => (v === 'block' ? <Tag color="red">阻断</Tag> : <Tag>告警</Tag>),
    },
    {
      title: '调度',
      dataIndex: 'schedule_cron',
      width: 110,
      ellipsis: true,
      render: (v: string) => v || <Typography.Text type="secondary">手动</Typography.Text>,
    },
    { title: '阈值', dataIndex: 'threshold', width: 72 },
    {
      title: '最近结果',
      width: 140,
      render: (_: unknown, row: RuleRow) => (
        row.latest_status
          ? (
            <Space size={4}>
              <Tag color={STATUS_COLOR[row.latest_status] || 'default'}>{row.latest_status}</Tag>
              <span>{row.latest_score ?? '—'}</span>
            </Space>
          )
          : <Typography.Text type="secondary">未检查</Typography.Text>
      ),
    },
    {
      title: '启用',
      width: 72,
      render: (_: unknown, row: RuleRow) => (
        <Switch size="small" checked={!!row.is_active} onChange={(checked) => { void handleToggleActive(row, checked) }} />
      ),
    },
    {
      title: '操作',
      width: 260,
      render: (_: unknown, row: RuleRow) => (
        <Space wrap>
          <Button size="small" icon={<PlayCircleOutlined />} disabled={!row.is_active} onClick={() => { void handleCheck(row.id) }}>
            检查
          </Button>
          <Button size="small" icon={<HistoryOutlined />} onClick={() => { void openHistory(row) }}>历史</Button>
          <Button size="small" icon={<EditOutlined />} onClick={() => { void openEdit(row) }}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => { void handleDelete(row.id) }}>删除</Button>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <h2>数据质量</h2>
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={4}><Statistic title="规则总数" value={dashboard.total_rules || 0} /></Col>
        <Col span={3}><Statistic title="启用中" value={dashboard.active_rules || 0} /></Col>
        <Col span={3}><Statistic title="定时" value={dashboard.scheduled_rules || 0} /></Col>
        <Col span={3}><Statistic title="最近通过" value={dashboard.pass || 0} valueStyle={{ color: '#52c41a' }} /></Col>
        <Col span={3}><Statistic title="最近失败" value={dashboard.fail || 0} valueStyle={{ color: '#ff4d4f' }} /></Col>
        <Col span={4}><Statistic title="未检查" value={dashboard.unchecked || 0} /></Col>
        <Col span={4}><Statistic title="通过率" value={dashboard.pass_rate || 'N/A'} /></Col>
      </Row>
      {trendHint ? (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message={trendHint} />
      ) : null}
      {Array.isArray(dashboard.top_failures) && dashboard.top_failures.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="最近失败规则"
          description={(
            <Space wrap>
              {dashboard.top_failures.map((item: any) => (
                <Button key={item.rule_id} size="small" type="link" onClick={() => {
                  const hit = rules.find((r) => r.id === item.rule_id)
                  if (hit) void openHistory(hit)
                }}
                >
                  {item.rule_name}（{item.score}）
                </Button>
              ))}
            </Space>
          )}
        />
      ) : null}

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate()}>新建规则</Button>
        <span style={{ color: '#666', fontSize: 13 }}>检查业务日期</span>
        <DatePicker
          value={dayjs(checkBizdate)}
          onChange={(v) => setCheckBizdate(v ? v.format('YYYY-MM-DD') : dayjs().subtract(1, 'day').format('YYYY-MM-DD'))}
          allowClear={false}
        />
        <span style={{ color: '#666', fontSize: 13 }}>
          生产：在工作流中加 QUALITY 节点并发布到 Dolphin（与 SYNC 同为内部回调）。本地 cron 仅在未启用 Dolphin 时兜底。
        </span>
      </div>

      <Table dataSource={rules} columns={columnsDef} rowKey="id" pagination={{ pageSize: 20 }} />

      <Modal
        title={editing ? '编辑质量规则' : '新建质量规则'}
        open={modalOpen}
        onOk={() => { void handleSave() }}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={720}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="rule_name" label="规则名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input maxLength={128} />
          </Form.Item>
          {!editing ? (
            <Form.Item name="table_key" label="关联表" rules={[{ required: true, message: '请选择表' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                options={tables.filter((t: any) => t.table_name && !t.error).map((t: any) => ({
                  label: t.qualified_name || `${t.catalog}.${t.table_name}`,
                  value: t.row_key,
                }))}
              />
            </Form.Item>
          ) : (
            <Form.Item label="关联表">
              <Input value={editing.qualified_name || String(editing.table_id)} disabled />
            </Form.Item>
          )}
          <Form.Item name="rule_type" label="规则类型" rules={[{ required: true }]}>
            <Select options={RULE_TYPES} disabled={!!editing} />
          </Form.Item>
          <Form.Item name="severity" label="失败策略" rules={[{ required: true }]}>
            <Radio.Group>
              <Radio.Button value="warn">仅告警</Radio.Button>
              <Radio.Button value="block">强规则（可阻断）</Radio.Button>
            </Radio.Group>
          </Form.Item>
          <Form.Item
            name="schedule_cron"
            label="本地兜底定时"
            extra="仅在工作空间未启用 Dolphin 时由本机 APScheduler 执行。生产请用工作流 HTTP 节点回调检查接口，与实例中心同一分工。"
          >
            <Input
              placeholder="0 2 * * *"
              allowClear
              addonBefore={(
                <Select
                  style={{ width: 140 }}
                  placeholder="预设"
                  options={CRON_PRESETS}
                  onChange={(v) => form.setFieldValue('schedule_cron', v || undefined)}
                />
              )}
            />
          </Form.Item>
          <Form.Item name="threshold" label="阈值" rules={[{ required: true }]} extra="例如 >=95、>90、==100">
            <Input placeholder=">=95" />
          </Form.Item>

          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="partition_column" label="分区字段（可选）">
              <AutoComplete options={columnOptions} placeholder="如 dt" style={{ width: 200 }} filterOption />
            </Form.Item>
            <Form.Item name="partition_value" label="分区值" extra="可用 {bizdate}">
              <Input placeholder="{bizdate}" style={{ width: 220 }} />
            </Form.Item>
          </Space>

          {NEEDS_COLUMN.has(ruleType) && ruleType !== 'timeliness' ? (
            <Form.Item name="column" label="字段" rules={[{ required: true, message: '请选择或填写字段' }]}>
              <AutoComplete options={columnOptions} placeholder={columns.length ? '选择或输入字段' : '输入字段名'} filterOption />
            </Form.Item>
          ) : null}

          {ruleType === 'timeliness' ? (
            <>
              <Form.Item name="time_column" label="时间字段" rules={[{ required: true }]}>
                <AutoComplete options={columnOptions} placeholder="如 updated_at" filterOption />
              </Form.Item>
              <Form.Item name="max_delay_hours" label="最大延迟（小时）" rules={[{ required: true }]}>
                <InputNumber min={1} max={720} style={{ width: '100%' }} />
              </Form.Item>
            </>
          ) : null}

          {ruleType === 'validity' ? (
            <>
              <Form.Item name="validity_mode" label="校验方式" rules={[{ required: true }]}>
                <Radio.Group>
                  <Radio.Button value="regex">正则</Radio.Button>
                  <Radio.Button value="enum">枚举</Radio.Button>
                  <Radio.Button value="range">数值范围</Radio.Button>
                </Radio.Group>
              </Form.Item>
              {validityMode === 'regex' ? (
                <Form.Item name="pattern" label="正则" rules={[{ required: true }]}>
                  <Input placeholder="^[0-9]+$" />
                </Form.Item>
              ) : null}
              {validityMode === 'enum' ? (
                <Form.Item name="allowed_values" label="允许值" rules={[{ required: true }]}>
                  <Select mode="tags" placeholder="回车添加" />
                </Form.Item>
              ) : null}
              {validityMode === 'range' ? (
                <Space style={{ display: 'flex' }} align="start">
                  <Form.Item name="min_value" label="最小值">
                    <InputNumber style={{ width: 160 }} />
                  </Form.Item>
                  <Form.Item name="max_value" label="最大值">
                    <InputNumber style={{ width: 160 }} />
                  </Form.Item>
                </Space>
              ) : null}
            </>
          ) : null}

          {ruleType === 'consistency' ? (
            <>
              <Form.Item name="left_sql" label="左侧 SQL" rules={[{ required: true }]} extra="返回一个数值；可用 {table} {bizdate}">
                <Input.TextArea rows={3} placeholder="SELECT COUNT(*) FROM {table} WHERE dt='{bizdate}'" />
              </Form.Item>
              <Form.Item name="right_sql" label="右侧 SQL" rules={[{ required: true }]}>
                <Input.TextArea rows={3} placeholder="SELECT COUNT(*) FROM other_db.other_table WHERE dt='{bizdate}'" />
              </Form.Item>
              <Form.Item name="tolerance_pct" label="容差（%）">
                <InputNumber min={0} max={100} style={{ width: 160 }} />
              </Form.Item>
            </>
          ) : null}

          {ruleType === 'accuracy' || ruleType === 'custom_sql' || ruleType === 'dolphin_sql' ? (
            <Form.Item
              name="sql"
              label="SQL（返回一个数值得分）"
              rules={[{ required: true }]}
              extra="{table} {bizdate} 会替换"
            >
              <Input.TextArea rows={4} placeholder="SELECT ROUND((1 - SUM(CASE WHEN id IS NULL THEN 1 ELSE 0 END)/COUNT(*))*100) FROM {table} WHERE dt='{bizdate}'" />
            </Form.Item>
          ) : null}

          <Form.Item
            name="dolphin_refs_json"
            label="Dolphin 联动 JSON（可选）"
            tooltip={{ title: '存 process_code / task_code，便于与编排任务对应' }}
          >
            <Input.TextArea rows={2} placeholder='{"process_code":"","task_code":""}' />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={historyRule ? `检查历史 · ${historyRule.rule_name}` : '检查历史'}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        width={640}
      >
        {sampleRows.length > 0 ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message={`最近一次失败抽样 ${sampleRows.length} 行`}
            description={(
              <pre style={{ margin: 0, maxHeight: 160, overflow: 'auto', fontSize: 12 }}>
                {JSON.stringify(sampleRows, null, 2)}
              </pre>
            )}
          />
        ) : null}
        <Table
          loading={historyLoading}
          dataSource={historyRows}
          rowKey="id"
          size="small"
          pagination={false}
          locale={{ emptyText: '还没有检查记录' }}
          columns={[
            {
              title: '时间',
              dataIndex: 'checked_at',
              width: 160,
              render: (v: string) => (v ? String(v).replace('T', ' ').slice(0, 19) : '—'),
            },
            {
              title: '结果',
              dataIndex: 'status',
              width: 88,
              render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s}</Tag>,
            },
            { title: '得分', dataIndex: 'score', width: 72 },
            {
              title: '明细',
              dataIndex: 'detail',
              ellipsis: true,
              render: (detail: any) => {
                if (!detail) return '—'
                if (detail.error) return String(detail.error)
                const bits = [
                  detail.partition ? `分区 ${detail.partition}` : '',
                  detail.bizdate ? `业务日 ${detail.bizdate}` : '',
                  detail.trigger ? `触发 ${detail.trigger}` : '',
                  detail.invalid != null ? `无效 ${detail.invalid}` : '',
                  detail.null_count != null ? `空值 ${detail.null_count}` : '',
                ].filter(Boolean)
                return bits.join(' · ') || JSON.stringify(detail)
              },
            },
          ]}
        />
      </Drawer>
    </div>
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-07-10
 *
 * 数据服务可视化向导：选表 → 选字段 → 配条件 → 预览 SQL，并同步入参。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Button, Checkbox, Col, Input, Row, Select, Space, Spin, Table, Tag, Typography, message,
} from 'antd'
import { ReloadOutlined, SwapOutlined } from '@ant-design/icons'
import { dataServiceApi } from '../../api'
import { formatApiError } from './shared'

const { Text, Paragraph } = Typography

export type WizardFilter = {
  column: string
  op: string
  param: string
  required?: boolean
  data_type?: string
  default_value?: string
}

export type WizardOrderBy = {
  column: string
  direction: 'ASC' | 'DESC'
}

export type WizardConfig = {
  table: string
  fields: string[]
  filters: WizardFilter[]
  /** 固定排序；空则引擎按返回字段前两列 ASC 保底（SELECT * 时须手动配） */
  order_by?: WizardOrderBy[]
}

export type WizardParam = {
  name: string
  param_in?: string
  data_type?: string
  required?: boolean
  default_value?: string
  description?: string
  sort_order?: number
}

type ColumnMeta = { name: string; type?: string; nullable?: boolean; key?: string }

const OPS = [
  { value: '=', label: '=' },
  { value: '!=', label: '!=' },
  { value: '>', label: '>' },
  { value: '>=', label: '>=' },
  { value: '<', label: '<' },
  { value: '<=', label: '<=' },
  { value: 'LIKE', label: 'LIKE' },
]

function guessDataType(colType?: string): string {
  const t = (colType || '').toLowerCase()
  if (!t) return 'string'
  if (t.includes('bool') || t === 'bit(1)') return 'bool'
  if (t.includes('int') && !t.includes('point')) return t.includes('big') ? 'long' : 'int'
  if (t.includes('decimal') || t.includes('numeric') || t.includes('double') || t.includes('float') || t.includes('real')) {
    return 'float'
  }
  if (t.startsWith('date') && !t.includes('time')) return 'date'
  if (t.includes('timestamp') || t.includes('datetime') || t.includes('time')) return 'datetime'
  return 'string'
}

function sanitizeParamName(col: string): string {
  const base = col.replace(/[^a-zA-Z0-9_]/g, '_').replace(/^(\d)/, '_$1')
  return base || 'param'
}

function buildLocalSql(cfg: WizardConfig): string {
  const table = (cfg.table || '').trim()
  if (!table) return '-- 请先选择表'
  const fields = cfg.fields?.length ? cfg.fields : ['*']
  const selectCols = fields.includes('*') ? '*' : fields.join(', ')
  const where = ['1=1']
  for (const f of cfg.filters || []) {
    if (!f.column || !f.param) continue
    const op = (f.op || '=').toUpperCase()
    where.push(`(${f.column} ${op} :${f.param} OR :${f.param} IS NULL)`)
  }
  let sql = `SELECT ${selectCols} FROM ${table} WHERE ${where.join(' AND ')}`
  const orderParts = resolveOrderByClauses(cfg)
  if (orderParts.length) {
    sql += ` ORDER BY ${orderParts.join(', ')}`
  }
  return sql
}

function resolveOrderByClauses(cfg: WizardConfig): string[] {
  const explicit = (cfg.order_by || []).filter(o => o.column)
  const items = explicit.length
    ? explicit
    : (cfg.fields || []).filter(f => f && f !== '*').slice(0, 2).map(column => ({
        column,
        direction: 'ASC' as const,
      }))
  const seen = new Set<string>()
  const parts: string[] = []
  for (const o of items) {
    const col = (o.column || '').trim()
    if (!col) continue
    const key = col.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    const dir = (o.direction || 'ASC').toUpperCase() === 'DESC' ? 'DESC' : 'ASC'
    parts.push(`${col} ${dir}`)
  }
  return parts
}

function filtersToParams(filters: WizardFilter[], columns: ColumnMeta[]): WizardParam[] {
  const typeByCol = new Map(columns.map(c => [c.name, guessDataType(c.type)]))
  return (filters || [])
    .filter(f => f.param && f.column)
    .map((f, i) => ({
      name: f.param,
      param_in: 'query',
      data_type: f.data_type || typeByCol.get(f.column) || 'string',
      required: !!f.required,
      default_value: f.default_value,
      description: `过滤列 ${f.column}`,
      sort_order: i,
    }))
}

type Props = {
  datasourceId?: number
  value?: WizardConfig | null
  onChange: (cfg: WizardConfig, params: WizardParam[], sqlPreview: string) => void
  onUpgradeToSql?: (sql: string, params: WizardParam[]) => void
}

export default function ApiWizardBuilder({ datasourceId, value, onChange, onUpgradeToSql }: Props) {
  const [tables, setTables] = useState<{ label: string; value: string }[]>([])
  const [columns, setColumns] = useState<ColumnMeta[]>([])
  const [loadingTables, setLoadingTables] = useState(false)
  const [loadingCols, setLoadingCols] = useState(false)
  const [sqlPreview, setSqlPreview] = useState('')
  const [previewError, setPreviewError] = useState<string | null>(null)
  const emitRef = useRef(onChange)
  emitRef.current = onChange

  const cfg: WizardConfig = useMemo(() => ({
    table: value?.table || '',
    fields: Array.isArray(value?.fields) ? value!.fields : [],
    filters: Array.isArray(value?.filters) ? value!.filters : [],
    order_by: Array.isArray(value?.order_by) ? value!.order_by : [],
  }), [value])

  const loadTables = useCallback(async (dsId: number) => {
    setLoadingTables(true)
    try {
      const res: any = await dataServiceApi.listTables(dsId)
      setTables((res?.tables || []).map((t: any) => ({
        value: t.name,
        label: t.comment ? `${t.name}（${t.comment}）` : t.name,
      })))
    } catch (e: any) {
      setTables([])
      message.error(formatApiError(e, '加载表列表失败'))
    } finally {
      setLoadingTables(false)
    }
  }, [])

  const loadColumns = useCallback(async (dsId: number, table: string) => {
    setLoadingCols(true)
    try {
      const res: any = await dataServiceApi.listColumns(dsId, table)
      setColumns(res?.columns || [])
    } catch (e: any) {
      setColumns([])
      message.error(formatApiError(e, '加载字段失败'))
    } finally {
      setLoadingCols(false)
    }
  }, [])

  useEffect(() => {
    if (!datasourceId) {
      setTables([])
      setColumns([])
      return
    }
    loadTables(datasourceId)
  }, [datasourceId, loadTables])

  useEffect(() => {
    if (!datasourceId || !cfg.table) {
      setColumns([])
      return
    }
    loadColumns(datasourceId, cfg.table)
  }, [datasourceId, cfg.table, loadColumns])

  const pushChange = useCallback((next: WizardConfig, colMeta: ColumnMeta[] = columns) => {
    const params = filtersToParams(next.filters, colMeta)
    const local = buildLocalSql(next)
    setSqlPreview(local)
    setPreviewError(null)
    emitRef.current(next, params, local)
  }, [columns])

  // 服务端预览（仅更新右侧 SQL，不回写表单，避免循环）
  const fieldsKey = cfg.fields.join(',')
  const filtersKey = JSON.stringify(cfg.filters.map(f => [f.column, f.op, f.param, !!f.required]))
  const orderByKey = JSON.stringify((cfg.order_by || []).map(o => [o.column, o.direction]))
  useEffect(() => {
    if (!cfg.table) {
      setSqlPreview('-- 请先选择表')
      setPreviewError(null)
      return
    }
    let cancelled = false
    const snapshot: WizardConfig = {
      table: cfg.table,
      fields: cfg.fields.slice(),
      filters: cfg.filters.map(f => ({ ...f })),
      order_by: (cfg.order_by || []).map(o => ({ ...o })),
    }
    const params = filtersToParams(snapshot.filters, columns)
    const timer = window.setTimeout(async () => {
      try {
        const res: any = await dataServiceApi.previewWizardSql({
          wizard_config: {
            table: snapshot.table,
            fields: snapshot.fields.length ? snapshot.fields : ['*'],
            filters: snapshot.filters.map(f => ({
              column: f.column,
              op: f.op || '=',
              param: f.param,
            })),
            order_by: (snapshot.order_by || [])
              .filter(o => o.column)
              .map(o => ({
                column: o.column,
                direction: (o.direction || 'ASC').toUpperCase() === 'DESC' ? 'DESC' : 'ASC',
              })),
          },
          params,
        })
        if (cancelled) return
        setPreviewError(null)
        setSqlPreview(res?.sql_template || buildLocalSql(snapshot))
      } catch (e: any) {
        if (cancelled) return
        setSqlPreview(buildLocalSql(snapshot))
        setPreviewError(formatApiError(e, 'SQL 预览失败'))
      }
    }, 280)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [cfg.table, fieldsKey, filtersKey, orderByKey, columns])

  const setTable = (table: string) => {
    pushChange({ table, fields: [], filters: [], order_by: [] })
  }

  const toggleField = (name: string, checked: boolean) => {
    const set = new Set(cfg.fields.filter(f => f !== '*'))
    if (checked) set.add(name)
    else set.delete(name)
    pushChange({ ...cfg, fields: Array.from(set) })
  }

  const selectAllFields = () => {
    pushChange({ ...cfg, fields: columns.map(c => c.name) })
  }

  const clearFields = () => {
    pushChange({ ...cfg, fields: [] })
  }

  const updateFilter = (index: number, patch: Partial<WizardFilter>) => {
    const filters = cfg.filters.map((f, i) => (i === index ? { ...f, ...patch } : f))
    pushChange({ ...cfg, filters })
  }

  const addFilter = () => {
    const col = columns[0]?.name || ''
    pushChange({
      ...cfg,
      filters: [
        ...cfg.filters,
        {
          column: col,
          op: '=',
          param: sanitizeParamName(col),
          required: false,
          data_type: guessDataType(columns[0]?.type),
        },
      ],
    })
  }

  const removeFilter = (index: number) => {
    pushChange({ ...cfg, filters: cfg.filters.filter((_, i) => i !== index) })
  }

  const updateOrderBy = (index: number, patch: Partial<WizardOrderBy>) => {
    const order_by = (cfg.order_by || []).map((o, i) => (i === index ? { ...o, ...patch } : o))
    pushChange({ ...cfg, order_by })
  }

  const addOrderBy = () => {
    const pk = columns.find(c => c.key === 'PRI')?.name
    const col = pk || columns[0]?.name || cfg.fields[0] || ''
    pushChange({
      ...cfg,
      order_by: [...(cfg.order_by || []), { column: col, direction: 'ASC' }],
    })
  }

  const removeOrderBy = (index: number) => {
    pushChange({ ...cfg, order_by: (cfg.order_by || []).filter((_, i) => i !== index) })
  }

  const fillOrderByFromPk = () => {
    const pks = columns.filter(c => c.key === 'PRI').map(c => c.name)
    if (!pks.length) {
      message.info('当前表未识别到主键，请手动添加排序列')
      return
    }
    pushChange({
      ...cfg,
      order_by: pks.map(column => ({ column, direction: 'ASC' as const })),
    })
  }

  if (!datasourceId) {
    return <Alert type="info" showIcon message="请先选择数据源，再可视化选表与字段" />
  }

  const selectedSet = new Set(cfg.fields)

  return (
    <div>
      <Row gutter={16}>
        <Col span={14}>
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <div>
              <Text type="secondary">数据表</Text>
              <Select
                showSearch
                allowClear
                style={{ width: '100%', marginTop: 6 }}
                placeholder="搜索并选择表"
                loading={loadingTables}
                options={tables}
                value={cfg.table || undefined}
                onChange={v => setTable(v || '')}
                optionFilterProp="label"
                notFoundContent={loadingTables ? <Spin size="small" /> : '无表'}
              />
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <Text type="secondary">返回字段（不选则 SELECT *）</Text>
                <Space size={4}>
                  <Button size="small" type="link" disabled={!columns.length} onClick={selectAllFields}>全选</Button>
                  <Button size="small" type="link" disabled={!cfg.fields.length} onClick={clearFields}>清空</Button>
                  <Button
                    size="small"
                    type="link"
                    icon={<ReloadOutlined />}
                    disabled={!cfg.table}
                    onClick={() => datasourceId && cfg.table && loadColumns(datasourceId, cfg.table)}
                  >
                    刷新
                  </Button>
                </Space>
              </div>
              <Spin spinning={loadingCols}>
                <div style={{
                  maxHeight: 200,
                  overflow: 'auto',
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  padding: '8px 12px',
                  background: '#fafafa',
                }}>
                  {!cfg.table && <Text type="secondary">先选择表</Text>}
                  {cfg.table && !columns.length && !loadingCols && <Text type="secondary">未获取到字段</Text>}
                  {columns.map(col => (
                    <div key={col.name} style={{ padding: '2px 0' }}>
                      <Checkbox
                        checked={selectedSet.has(col.name)}
                        onChange={e => toggleField(col.name, e.target.checked)}
                      >
                        <Text code style={{ fontSize: 12 }}>{col.name}</Text>
                        {col.type && <Text type="secondary" style={{ marginLeft: 8, fontSize: 11 }}>{col.type}</Text>}
                        {col.key === 'PRI' && <Tag color="blue" style={{ marginLeft: 6, fontSize: 10, lineHeight: '16px' }}>PK</Tag>}
                      </Checkbox>
                    </div>
                  ))}
                </div>
              </Spin>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <Text type="secondary">过滤条件（自动生成请求参数）</Text>
                <Button size="small" type="dashed" disabled={!cfg.table} onClick={addFilter}>+ 条件</Button>
              </div>
              <Table
                size="small"
                pagination={false}
                rowKey={(_, i) => String(i)}
                dataSource={cfg.filters}
                locale={{ emptyText: '无过滤条件时返回全表（受分页/行数上限约束）' }}
                columns={[
                  {
                    title: '列',
                    width: 140,
                    render: (_: any, row: WizardFilter, index: number) => (
                      <Select
                        size="small"
                        style={{ width: '100%' }}
                        showSearch
                        options={columns.map(c => ({ value: c.name, label: c.name }))}
                        value={row.column || undefined}
                        onChange={col => {
                          const meta = columns.find(c => c.name === col)
                          updateFilter(index, {
                            column: col,
                            param: sanitizeParamName(col),
                            data_type: guessDataType(meta?.type),
                          })
                        }}
                      />
                    ),
                  },
                  {
                    title: '运算符',
                    width: 88,
                    render: (_: any, row: WizardFilter, index: number) => (
                      <Select
                        size="small"
                        style={{ width: '100%' }}
                        options={OPS}
                        value={row.op || '='}
                        onChange={op => updateFilter(index, { op })}
                      />
                    ),
                  },
                  {
                    title: '参数名',
                    width: 120,
                    render: (_: any, row: WizardFilter, index: number) => (
                      <Input
                        size="small"
                        value={row.param}
                        onChange={e => updateFilter(index, { param: sanitizeParamName(e.target.value) })}
                      />
                    ),
                  },
                  {
                    title: '必填',
                    width: 64,
                    render: (_: any, row: WizardFilter, index: number) => (
                      <Checkbox
                        checked={!!row.required}
                        onChange={e => updateFilter(index, { required: e.target.checked })}
                      />
                    ),
                  },
                  {
                    title: '',
                    width: 48,
                    render: (_: any, __: WizardFilter, index: number) => (
                      <Button type="link" danger size="small" onClick={() => removeFilter(index)}>删</Button>
                    ),
                  },
                ]}
              />
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <Text type="secondary">排序（固定列表顺序，建议含主键）</Text>
                <Space size={4}>
                  <Button size="small" type="link" disabled={!cfg.table || !columns.length} onClick={fillOrderByFromPk}>
                    用主键
                  </Button>
                  <Button size="small" type="dashed" disabled={!cfg.table} onClick={addOrderBy}>+ 排序</Button>
                </Space>
              </div>
              <Table
                size="small"
                pagination={false}
                rowKey={(_, i) => String(i)}
                dataSource={cfg.order_by || []}
                locale={{ emptyText: '未配置时：若选了返回字段，默认按前两列 ASC；SELECT * 请手动加排序' }}
                columns={[
                  {
                    title: '列',
                    width: 160,
                    render: (_: any, row: WizardOrderBy, index: number) => (
                      <Select
                        size="small"
                        style={{ width: '100%' }}
                        showSearch
                        options={columns.map(c => ({ value: c.name, label: c.name }))}
                        value={row.column || undefined}
                        onChange={column => updateOrderBy(index, { column })}
                      />
                    ),
                  },
                  {
                    title: '方向',
                    width: 100,
                    render: (_: any, row: WizardOrderBy, index: number) => (
                      <Select
                        size="small"
                        style={{ width: '100%' }}
                        options={[
                          { value: 'ASC', label: '升序' },
                          { value: 'DESC', label: '降序' },
                        ]}
                        value={row.direction || 'ASC'}
                        onChange={direction => updateOrderBy(index, { direction })}
                      />
                    ),
                  },
                  {
                    title: '',
                    width: 48,
                    render: (_: any, __: WizardOrderBy, index: number) => (
                      <Button type="link" danger size="small" onClick={() => removeOrderBy(index)}>删</Button>
                    ),
                  },
                ]}
              />
            </div>
          </Space>
        </Col>

        <Col span={10}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <Text type="secondary">SQL 预览</Text>
            {onUpgradeToSql && (
              <Button
                size="small"
                icon={<SwapOutlined />}
                disabled={!cfg.table}
                onClick={() => {
                  const params = filtersToParams(cfg.filters, columns)
                  const sql = sqlPreview && !sqlPreview.startsWith('--') ? sqlPreview : buildLocalSql(cfg)
                  onUpgradeToSql(sql, params)
                }}
              >
                升级为 SQL 模式
              </Button>
            )}
          </div>
          {previewError && <Alert type="warning" showIcon message={previewError} style={{ marginBottom: 8 }} />}
          <pre style={{
            margin: 0,
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 6,
            minHeight: 220,
            maxHeight: 360,
            overflow: 'auto',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}>
            {sqlPreview || '--'}
          </pre>
          <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
            保存后以向导配置为准，发布时由引擎重新编译 SQL（含 ORDER BY）。复杂查询可升级为 SQL 模式（单向，不可逆向）。
          </Paragraph>
        </Col>
      </Row>
    </div>
  )
}

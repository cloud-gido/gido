/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Drawer, Empty, Input, Pagination, Select, Space, Spin, Tabs, Tag, Tooltip, Tree, message } from 'antd'
import { CopyOutlined, DownloadOutlined, ExperimentOutlined, LinkOutlined, StopOutlined } from '@ant-design/icons'
import { adhocRunsApi } from '../api'
import type { useInteractiveRun } from '../hooks/useInteractiveRun'
import type {
  InteractiveExportFormat,
  InteractiveRunExport,
  InteractiveRunShare,
  InteractiveRunStatement,
  InteractiveRowsQueryRequest,
  InteractiveStatementExplain,
} from '../types/interactiveRun'
import EditorResultDock, { EditorResultRowBadge } from './EditorResultDock'
import LiveRunPanel from './LiveRunPanel'
import StatementResultTabs from './StatementResultTabs'
import StatementExecutionSummary from './StatementExecutionSummary'
import QueryResultPanel from './QueryResultPanel'
import { buildQueryTableColumns, rowsToRecordDataSource } from './QueryResultTable'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { pruneWidths, resolveResultColumnOrder } from '../utils/resultTableMeta'
import { statementPresentation } from '../utils/statementPresentation'
import { SQL_RESULT_ROW_CAP } from '../utils/sqlResultRowLimit'
import { useInteractiveStatementQuery } from '../hooks/useInteractiveStatementQuery'
import type { QueryResultServerChange } from './QueryResultPanel'
import { NULL_FILTER_KEY } from './ColumnFilterDropdown'

type RunController = ReturnType<typeof useInteractiveRun>
type TabKey = 'log' | 'result'
type Layout = { order: string[]; widths: Record<string, number>; sourceKeys?: string[] }
const RESULT_PAGE_SIZE = Math.min(200, SQL_RESULT_ROW_CAP)

function explainPlanTree(snapshot: unknown): Array<{ key: string; title: string; children?: any[] }> {
  const rows = snapshot && typeof snapshot === 'object' && Array.isArray((snapshot as any).rows)
    ? (snapshot as any).rows as unknown[]
    : []
  const roots: Array<{ key: string; title: string; children?: any[] }> = []
  const stack: Array<{ depth: number; node: { key: string; title: string; children?: any[] } }> = []
  rows.forEach((row, index) => {
    const values = Array.isArray(row) ? row : [row]
    const title = values.map(value => typeof value === 'string' ? value : JSON.stringify(value)).join(' | ')
    const leading = title.match(/^\s*/)?.[0].length ?? 0
    const depth = title.includes('->') ? Math.max(1, Math.floor(leading / 2) + 1) : 0
    const node = { key: String(index), title: title.trim() || '(空计划行)' }
    while (stack.length && stack[stack.length - 1].depth >= depth) stack.pop()
    if (stack.length) {
      stack[stack.length - 1].node.children ??= []
      stack[stack.length - 1].node.children!.push(node)
    } else {
      roots.push(node)
    }
    stack.push({ depth, node })
  })
  return roots
}

const STATEMENT_META: Record<string, { color: string; label: string }> = {
  pending: { color: 'default', label: '等待' },
  queued: { color: 'blue', label: '排队' },
  running: { color: 'processing', label: '运行中' },
  success: { color: 'green', label: '成功' },
  failed: { color: 'red', label: '失败' },
  skipped: { color: 'default', label: '跳过' },
  cancelled: { color: 'default', label: '停止' },
}

function duration(statement: Pick<InteractiveRunStatement, 'started_at' | 'finished_at'>): string {
  if (!statement.started_at) return '—'
  const end = statement.finished_at ? Date.parse(statement.finished_at) : Date.now()
  const value = Math.max(0, end - Date.parse(statement.started_at))
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(2)}s`
}

function formatBytes(value?: number | null): string {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / 1024 ** 2).toFixed(1)} MiB`
}

function layoutKey(scopeKey: string, statement: InteractiveRunStatement): string {
  const fingerprint = (statement.fields?.map(field => field.name) ?? statement.columns).join('\x1f')
  return `gido.interactiveRun.columns.v1:${scopeKey}:${statement.index}:${fingerprint}`
}

function readLayout(key: string): Layout {
  try {
    return JSON.parse(localStorage.getItem(key) || '') as Layout
  } catch {
    return { order: [], widths: {} }
  }
}

export default function InteractiveRunDock({
  run,
  scopeKey,
  onClose,
  compact = false,
  autoShowResult = true,
}: {
  run: RunController
  scopeKey: string
  onClose?: () => void
  compact?: boolean
  autoShowResult?: boolean
}) {
  const [internalTab, setInternalTab] = useState<TabKey>('log')
  const [manualTab, setManualTab] = useState(false)
  const [statementKey, setStatementKey] = useState('0')
  const [search, setSearch] = useState('')
  const [serverFilters, setServerFilters] = useState<Record<string, string[]>>({})
  const [filters, setFilters] = useState<NonNullable<InteractiveRowsQueryRequest['filters']>>([])
  const [sort, setSort] = useState<NonNullable<InteractiveRowsQueryRequest['sort']>>([])
  const [explain, setExplain] = useState<InteractiveStatementExplain | null>(null)
  const [explainOpen, setExplainOpen] = useState(false)
  const [explainLoading, setExplainLoading] = useState(false)
  const [exportJob, setExportJob] = useState<InteractiveRunExport | null>(null)
  const [share, setShare] = useState<InteractiveRunShare | null>(null)
  const [shareUrl, setShareUrl] = useState('')
  const [shareTtlHours, setShareTtlHours] = useState(24)
  const [shareLoading, setShareLoading] = useState(false)
  const [layout, setLayout] = useState<Layout>({ order: [], widths: {} })
  const previousRunRef = useRef<number | null>(run.runId)
  const exportGenerationRef = useRef(0)
  const exportControllerRef = useRef<AbortController | null>(null)
  const selected = run.statements.find(item => String(item.index) === statementKey) ?? run.statements[0] ?? null
  const selectedKey = selected ? String(selected.index) : statementKey
  const previousSelectionRef = useRef(selectedKey)
  const currentTab = internalTab
  const query = useInteractiveStatementQuery({
    runId: run.runId,
    statementIndex: selected?.index ?? null,
    statementVersion: selected?.statement_version,
    search,
    filters,
    sort,
    limit: RESULT_PAGE_SIZE,
  })
  const page = query.data
  const displayedPlan = explain?.plan_snapshot ?? selected?.plan_snapshot
  const planTree = useMemo(() => explainPlanTree(displayedPlan), [displayedPlan])

  const changeTab = (key: TabKey, manual = true) => {
    if (manual) setManualTab(true)
    setInternalTab(key)
  }

  useEffect(() => {
    if (previousRunRef.current === run.runId) return
    previousRunRef.current = run.runId
    setManualTab(false)
    setInternalTab('log')
    setStatementKey('0')
    setSearch('')
    setServerFilters({})
    setFilters([])
    setSort([])
    setExplain(null)
    setExportJob(null)
    setShare(null)
    setShareUrl('')
    exportGenerationRef.current += 1
    exportControllerRef.current?.abort()
    exportControllerRef.current = null
  }, [run.runId])

  useEffect(() => {
    if (!selected || run.statements.some(item => String(item.index) === statementKey)) return
    setStatementKey(String(run.statements.find(item => item.columns.length || item.fields?.length)?.index ?? run.statements[0]?.index ?? 0))
  }, [run.statements, selected, statementKey])

  useEffect(() => {
    const completedOutcome = run.status === 'success' && run.statements.length > 0
    if (
      !autoShowResult
      || manualTab
      || (!run.capabilities.hasResults && !completedOutcome)
    ) return
    changeTab('result', false)
  }, [
    autoShowResult,
    manualTab,
    run.status,
    run.statements.length,
    run.capabilities.hasResults,
  ])

  useEffect(() => {
    if (previousSelectionRef.current === selectedKey) return
    previousSelectionRef.current = selectedKey
    setSearch('')
    setServerFilters({})
    setFilters([])
    setSort([])
    setExplain(null)
    setExportJob(null)
    exportGenerationRef.current += 1
    exportControllerRef.current?.abort()
    exportControllerRef.current = null
  }, [run.runId, selectedKey])

  useEffect(() => () => {
    exportGenerationRef.current += 1
    exportControllerRef.current?.abort()
  }, [])

  useEffect(() => {
    if (!selected) return
    const key = layoutKey(scopeKey, selected)
    setLayout(readLayout(key))
  }, [scopeKey, selected?.index, selected?.columns.join('\x1f'), selected?.fields])

  const dataSource = useMemo(
    () => rowsToRecordDataSource(page.fields.map(field => field.name), page.rows),
    [page],
  )
  const tableColumns = useMemo(() => {
    const fields = page.fields.length
      ? page.fields
      : (selected?.fields ?? normalizeQueryColumns(selected?.columns ?? [], selected?.column_types ?? []))
    const names = fields.map(field => field.name)
    const persist = (next: Layout) => {
      setLayout(next)
      if (selected) localStorage.setItem(layoutKey(scopeKey, selected), JSON.stringify(next))
    }
    const normalizedOrder = resolveResultColumnOrder(layout.order, names, layout.sourceKeys)
    const normalizedWidths = pruneWidths(layout.widths, names)
    return buildQueryTableColumns(fields, {
      order: normalizedOrder,
      widths: normalizedWidths,
      dataSource,
      serverQuery: true,
      serverFilters,
      onOrderChange: order => persist({ order, widths: normalizedWidths, sourceKeys: names }),
      onWidthChange: (key, width) => persist({
        order: normalizedOrder,
        widths: { ...normalizedWidths, [key]: width },
        sourceKeys: names,
      }),
    })
  }, [page, dataSource, layout, scopeKey, selected, serverFilters])
  const sortOptions = useMemo(() => {
    const names = page.fields.length
      ? page.fields.map(field => field.name)
      : (selected?.fields?.map(field => field.name) ?? selected?.columns ?? [])
    return names.flatMap(column => ([
      { value: JSON.stringify({ column, direction: 'asc' }), label: `${column} ↑` },
      { value: JSON.stringify({ column, direction: 'desc' }), label: `${column} ↓` },
    ]))
  }, [page.fields, selected?.columns, selected?.fields])

  const handleServerChange = ({ filters: nextFilters, sort: nextSort }: QueryResultServerChange) => {
    const controlled: Record<string, string[]> = {}
    const requestFilters: NonNullable<InteractiveRowsQueryRequest['filters']> = []
    Object.entries(nextFilters).forEach(([column, values]) => {
      const keys = (values ?? []).map(String)
      if (!keys.length) return
      controlled[column] = keys
      const selectedValues = keys
        .filter(value => !value.startsWith('__contains:'))
        .map(value => value === NULL_FILTER_KEY ? null : value)
      if (selectedValues.length) {
        requestFilters.push({ column, operator: 'in', value: selectedValues })
      }
      keys
        .filter(value => value.startsWith('__contains:'))
        .forEach(value => requestFilters.push({
          column,
          operator: 'contains',
          value: value.slice('__contains:'.length),
        }))
    })
    setServerFilters(controlled)
    setFilters(requestFilters)
    setSort(nextSort ? [nextSort] : [])
  }

  const requestExplain = async () => {
    if (!run.runId || !selected) return
    setExplainLoading(true)
    try {
      setExplain(await adhocRunsApi.explainStatement(run.runId, selected.index))
      setExplainOpen(true)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取执行计划失败')
    } finally {
      setExplainLoading(false)
    }
  }

  const requestExport = async (format: InteractiveExportFormat) => {
    if (!run.runId || !selected) return
    exportControllerRef.current?.abort()
    const controller = new AbortController()
    exportControllerRef.current = controller
    const generation = ++exportGenerationRef.current
    setExportJob(null)
    try {
      let job = await adhocRunsApi.createExport(run.runId, {
        statement_index: selected.index,
        format,
        search: search.trim() || undefined,
        filters: filters.length ? filters : undefined,
        sort: sort.length ? sort : undefined,
        statement_version: page.statement_version || selected.statement_version,
      })
      if (controller.signal.aborted || generation !== exportGenerationRef.current) return
      setExportJob(job)
      while (['queued', 'running', 'cancel_requested'].includes(job.status)) {
        await new Promise(resolve => window.setTimeout(resolve, 600))
        if (controller.signal.aborted || generation !== exportGenerationRef.current) return
        job = await adhocRunsApi.getExport(run.runId, job.id, controller.signal)
        if (controller.signal.aborted || generation !== exportGenerationRef.current) return
        setExportJob(job)
      }
      if (job.status === 'success') message.success(`${format.toUpperCase()} 导出已就绪`)
      else if (job.status === 'failed') message.error(job.error_message || '导出失败')
      else if (job.status === 'cancelled') message.info('导出已取消')
      else if (job.status === 'expired') message.warning('导出文件已过期，请重新导出')
    } catch (error: any) {
      if (error?.code !== 'ERR_CANCELED' && !controller.signal.aborted) {
        message.error(error?.response?.data?.detail || error?.message || '创建导出失败')
      }
    } finally {
      if (exportControllerRef.current === controller) exportControllerRef.current = null
    }
  }

  const downloadExport = async () => {
    if (!run.runId || !exportJob?.download_ready) return
    try {
      const blob = await adhocRunsApi.downloadExport(run.runId, exportJob.id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = exportJob.file_name || `run-${run.runId}.${exportJob.format}`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '下载导出文件失败')
    }
  }

  const cancelExport = async () => {
    if (!run.runId || !exportJob) return
    try {
      exportGenerationRef.current += 1
      exportControllerRef.current?.abort()
      const cancelled = await adhocRunsApi.cancelExport(run.runId, exportJob.id)
      setExportJob(cancelled)
      message.info('已请求取消导出')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '取消导出失败')
    }
  }

  const createShare = async () => {
    if (!run.runId) return
    setShareLoading(true)
    try {
      const created = await adhocRunsApi.createShare(run.runId, shareTtlHours)
      if (!created.token) throw new Error('后端未返回分享 token')
      const url = `${window.location.origin}/gido/share/run/${encodeURIComponent(created.token)}`
      setShare(created)
      setShareUrl(url)
      try {
        await navigator.clipboard.writeText(url)
        message.success('空间内分享链接已创建并复制')
      } catch {
        message.success('空间内分享链接已创建，可点击复制链接')
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '创建分享链接失败')
    } finally {
      setShareLoading(false)
    }
  }

  const copyShare = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl)
      message.success('分享链接已复制')
    } catch {
      message.error('复制分享链接失败')
    }
  }

  const revokeShare = async () => {
    if (!run.runId || !share) return
    setShareLoading(true)
    try {
      const revoked = await adhocRunsApi.revokeShare(run.runId, share.id)
      setShare(revoked)
      setShareUrl('')
      message.success('分享链接已撤销')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '撤销分享链接失败')
    } finally {
      setShareLoading(false)
    }
  }

  const resultBody = selected ? (
    <StatementResultTabs
      statements={run.statements}
      activeKey={selectedKey}
      onChange={setStatementKey}
    >
      {statement => statement ? (
        <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <Space size={6} wrap style={{ padding: '6px 12px', borderBottom: '1px solid #f0f0f0' }}>
            <Tag color={(STATEMENT_META[statement.status] ?? STATEMENT_META.pending).color}>
              {(STATEMENT_META[statement.status] ?? STATEMENT_META.pending).label}
            </Tag>
            <Tag>{statementPresentation(statement).type}</Tag>
            <span>耗时 {duration(statement)}</span>
            {statementPresentation(statement).kind === 'dml'
              && statement.affected_rows != null
              && statement.affected_rows >= 0 && (
              <span>影响 {statement.affected_rows} 行</span>
              )}
            {statementPresentation(statement).kind === 'query' && <span>结果 {statement.total} 行</span>}
            {statement.truncated && <Tag color="orange">已截断</Tag>}
            {statement.query_id && <span title={statement.query_id}>Query ID: {statement.query_id}</span>}
            <span>
              执行 {statement.execution_metrics?.execution_ms != null
                ? `${statement.execution_metrics.execution_ms}ms`
                : statement.execution_metrics?.duration_ms != null
                  ? `${statement.execution_metrics.duration_ms}ms`
                  : duration(statement)}
            </span>
            {statement.execution_metrics?.fetch_ms != null && <span>取数 {statement.execution_metrics.fetch_ms}ms</span>}
            <span>结果大小 {formatBytes(statement.execution_metrics?.result_bytes ?? statement.result_bytes)}</span>
            {statementPresentation(statement).kind === 'query' && (
              <Button
                size="small"
                type="link"
                icon={<ExperimentOutlined />}
                loading={explainLoading}
                onClick={() => void requestExplain()}
              >
                Explain
              </Button>
            )}
          </Space>
          {!statement.error && (statement.columns.length || statement.fields?.length) ? (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              {query.loading ? <Spin style={{ margin: 24 }} /> : (
                <QueryResultPanel
                  dataSource={dataSource}
                  columns={tableColumns}
                  pagination={false}
                  serverQuery
                  serverSort={sort[0] ?? null}
                  onServerChange={handleServerChange}
                  toolbar={(
                    <Space wrap>
                      <Input.Search
                        allowClear
                        size="small"
                        aria-label="全局搜索查询结果"
                        placeholder="全局搜索"
                        value={search}
                        onChange={event => setSearch(event.target.value)}
                        style={{ width: 220 }}
                      />
                      <Select
                        mode="multiple"
                        aria-label="多列排序"
                        placeholder="多列排序（最多 2 列）"
                        value={sort.map(item => JSON.stringify(item))}
                        options={sortOptions}
                        style={{ minWidth: 220, maxWidth: 420 }}
                        maxTagCount={2}
                        onChange={values => {
                          const parsed = values
                            .map(value => JSON.parse(value) as { column: string; direction: 'asc' | 'desc' })
                            .filter((item, index, all) => (
                              all.slice(index + 1).every(candidate => candidate.column !== item.column)
                            ))
                            .slice(-2)
                          setSort(parsed)
                        }}
                      />
                      <span>
                        第 {query.pageNumber} 页 · 本页 {page.rows.length} ·
                        筛选 {page.total} / 总计 {page.source_total} 行
                      </span>
                      {(statement.truncated || page.truncated) && <Tag color="orange">结果已截断</Tag>}
                      <Pagination
                        size="small"
                        simple
                        current={query.pageNumber}
                        pageSize={RESULT_PAGE_SIZE}
                        showSizeChanger={false}
                        total={Math.max(
                          page.total,
                          (query.pageNumber - 1) * RESULT_PAGE_SIZE + page.rows.length + (page.has_more ? 1 : 0),
                        )}
                        onChange={next => {
                          if (next < query.pageNumber) query.previous()
                          else if (next === query.pageNumber + 1) query.next()
                        }}
                      />
                      <Button
                        size="small"
                        disabled={Boolean(exportJob && ['queued', 'running', 'cancel_requested'].includes(exportJob.status))}
                        onClick={() => void requestExport('csv')}
                      >
                        导出 CSV
                      </Button>
                      <Button
                        size="small"
                        disabled={Boolean(exportJob && ['queued', 'running', 'cancel_requested'].includes(exportJob.status))}
                        onClick={() => void requestExport('jsonl')}
                      >
                        导出 JSONL
                      </Button>
                      <Button
                        size="small"
                        disabled={Boolean(exportJob && ['queued', 'running', 'cancel_requested'].includes(exportJob.status))}
                        onClick={() => void requestExport('xlsx')}
                      >
                        导出 XLSX
                      </Button>
                      <Button
                        size="small"
                        disabled={Boolean(exportJob && ['queued', 'running', 'cancel_requested'].includes(exportJob.status))}
                        onClick={() => void requestExport('parquet')}
                      >
                        导出 Parquet
                      </Button>
                      <Tooltip title="导出基于已物化的不可变语句结果，并应用当前搜索、筛选与排序">
                        <Tag color="blue">已物化快照</Tag>
                      </Tooltip>
                      {exportJob && <Tag>{exportJob.status}</Tag>}
                      {exportJob && ['queued', 'running', 'cancel_requested'].includes(exportJob.status) && (
                        <Button
                          size="small"
                          icon={<StopOutlined />}
                          disabled={exportJob.status === 'cancel_requested'}
                          onClick={() => void cancelExport()}
                        >
                          取消导出
                        </Button>
                      )}
                      {exportJob?.download_ready && (
                        <Button size="small" icon={<DownloadOutlined />} onClick={() => void downloadExport()}>下载</Button>
                      )}
                    </Space>
                  )}
                />
              )}
              {query.error && <div style={{ padding: 12, color: '#ff4d4f' }}>{query.error}</div>}
            </div>
          ) : (
            <StatementExecutionSummary statement={statement} />
          )}
        </div>
      ) : null}
    </StatementResultTabs>
  ) : (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={run.isActive ? '运行中，结果可用后会提示' : '暂无语句结果'}
    />
  )

  return (
    <>
      <EditorResultDock
        activeKey={currentTab}
        onChange={key => changeTab(key as TabKey)}
        onClose={onClose ?? (() => undefined)}
        closeTitle={onClose ? '关闭结果面板' : '结果面板'}
        extra={(
          <Space size={4}>
            {!share?.active ? (
              <>
                <Select
                  size="small"
                  aria-label="分享链接有效期"
                  value={shareTtlHours}
                  style={{ width: 92 }}
                  onChange={setShareTtlHours}
                  options={[
                    { value: 1, label: '1 小时' },
                    { value: 24, label: '1 天' },
                    { value: 24 * 7, label: '7 天' },
                    { value: 24 * 30, label: '30 天' },
                  ]}
                />
                <Button
                  size="small"
                  icon={<LinkOutlined />}
                  loading={shareLoading}
                  disabled={!run.runId}
                  onClick={() => void createShare()}
                >
                  空间内分享
                </Button>
              </>
            ) : (
              <>
                <Tooltip title={`有效至 ${new Date(share.expires_at).toLocaleString()}`}>
                  <Tag color="green">分享有效</Tag>
                </Tooltip>
                <Button size="small" icon={<CopyOutlined />} onClick={() => void copyShare()}>复制链接</Button>
                <Button size="small" danger loading={shareLoading} onClick={() => void revokeShare()}>撤销</Button>
              </>
            )}
          </Space>
        )}
        tabs={[
        {
          key: 'log',
          label: <>日志 {run.isActive && <Spin size="small" style={{ marginLeft: 6 }} />}</>,
          children: (
            <LiveRunPanel
              compact={compact}
              runId={run.runId}
              status={run.status}
              log={run.log}
              error={run.error}
              isActive={run.isActive}
              onCancel={run.cancel}
              queueDurationMs={run.queueDurationMs}
              executionDurationMs={run.executionDurationMs}
            />
          ),
        },
        {
          key: 'result',
          label: (
            <Tooltip title={run.isActive && run.capabilities.hasResults ? '已有语句结果可查看' : undefined}>
              <span>
                运行结果
                {selected && statementPresentation(selected).kind === 'query' && (
                  <EditorResultRowBadge count={selected.total} />
                )}
                {run.isActive && run.capabilities.hasResults && <Tag color="green" style={{ marginLeft: 6 }}>可用</Tag>}
              </span>
            </Tooltip>
          ),
          children: resultBody,
        },
        ]}
      />
      <Drawer
        title={`执行计划${selected?.query_id ? ` · ${selected.query_id}` : ''}`}
        width={760}
        open={explainOpen}
        onClose={() => setExplainOpen(false)}
      >
        {planTree.length ? (
          <Tabs
            items={[
              {
                key: 'tree',
                label: '结构化计划',
                children: <Tree treeData={planTree} defaultExpandAll selectable={false} />,
              },
              {
                key: 'raw',
                label: '原始计划',
                children: (
                  <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                    {typeof displayedPlan === 'string'
                      ? displayedPlan
                      : JSON.stringify(displayedPlan ?? {}, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
        ) : (
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {typeof displayedPlan === 'string'
              ? displayedPlan
              : JSON.stringify(displayedPlan ?? {}, null, 2)}
          </pre>
        )}
      </Drawer>
    </>
  )
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Drawer, Dropdown, Empty, Input, InputNumber, Select, Space, Spin, Tabs, Tag, Tooltip, Tree, Checkbox, message } from 'antd'
import { CopyOutlined, ExperimentOutlined, LinkOutlined, TableOutlined, PushpinOutlined, SortAscendingOutlined, ClearOutlined } from '@ant-design/icons'
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
import { pruneNamedList, pruneWidths, resolveResultColumnOrder, MAX_PINNED_RESULT_COLUMNS } from '../utils/resultTableMeta'
import { statementPresentation } from '../utils/statementPresentation'
import { SQL_RESULT_ROW_CAP } from '../utils/sqlResultRowLimit'
import { buildExplainPlanTree } from '../utils/explainPlanTree'
import { useInteractiveStatementQuery } from '../hooks/useInteractiveStatementQuery'
import type { QueryResultServerChange } from './QueryResultPanel'
import { NULL_FILTER_KEY, valueToFilterKey } from './ColumnFilterDropdown'
import InteractiveExportButton, {
  type InteractiveExportDownloadState,
} from './InteractiveExportButton'
import './interactiveRunDock.css'

type RunController = ReturnType<typeof useInteractiveRun>
type TabKey = 'log' | 'result'
type Layout = {
  order: string[]
  widths: Record<string, number>
  hidden?: string[]
  pinned?: string[]
  sourceKeys?: string[]
}
const RESULT_PAGE_SIZE = Math.min(200, SQL_RESULT_ROW_CAP)
const EXPORT_FORMAT_STORAGE_KEY = 'gido.interactiveRun.exportFormat'
const STATEMENT_TERMINAL = new Set(['success', 'failed', 'cancelled', 'skipped'])

function readExportFormat(): InteractiveExportFormat {
  try {
    const value = localStorage.getItem(EXPORT_FORMAT_STORAGE_KEY)
    if (value === 'csv' || value === 'jsonl' || value === 'xlsx' || value === 'parquet') return value
  } catch {
    // Storage may be unavailable in private/restricted browser contexts.
  }
  return 'csv'
}

function explainTreeData(snapshot: unknown) {
  return buildExplainPlanTree(snapshot).map(function mapNode(node): any {
    const tags = []
    if (node.metrics?.cost) tags.push(<Tag key="cost" style={{ marginInlineStart: 6 }}>cost={node.metrics.cost}</Tag>)
    if (node.metrics?.rows) tags.push(<Tag key="rows" style={{ marginInlineStart: 4 }}>rows={node.metrics.rows}</Tag>)
    return {
      key: node.key,
      title: (
        <span>
          <span style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}>{node.title}</span>
          {tags}
        </span>
      ),
      children: node.children?.map(mapNode),
    }
  })
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
    return { order: [], widths: {}, hidden: [], pinned: [] }
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
  const [exportFormat, setExportFormat] = useState<InteractiveExportFormat>(readExportFormat)
  const [exportStarting, setExportStarting] = useState(false)
  const [exportDownloadState, setExportDownloadState] = useState<InteractiveExportDownloadState>('idle')
  const [share, setShare] = useState<InteractiveRunShare | null>(null)
  const [shareUrl, setShareUrl] = useState('')
  const [shareTtlHours, setShareTtlHours] = useState(24)
  const [shareAction, setShareAction] = useState<'create' | 'revoke' | null>(null)
  const [layout, setLayout] = useState<Layout>({ order: [], widths: {}, hidden: [], pinned: [] })
  const previousRunRef = useRef<number | null>(run.runId)
  const exportGenerationRef = useRef(0)
  const exportControllerRef = useRef<AbortController | null>(null)
  const exportCacheRef = useRef(new Map<string, InteractiveRunExport>())
  const selected = run.statements.find(item => String(item.index) === statementKey) ?? run.statements[0] ?? null
  const selectedKey = selected ? String(selected.index) : statementKey
  const canExportSelected = Boolean(
    selected
    && STATEMENT_TERMINAL.has(selected.status)
    && (selected.columns.length > 0 || Boolean(selected.fields?.length)),
  )
  const previousSelectionRef = useRef(selectedKey)
  const currentTab = internalTab
  const query = useInteractiveStatementQuery({
    runId: run.runId,
    statementIndex: selected?.index ?? null,
    statementVersion: selected?.statement_version,
    statementStatus: selected?.status,
    search,
    filters,
    sort,
    limit: RESULT_PAGE_SIZE,
  })
  const page = query.data
  const displayedPlan = explain?.plan_snapshot ?? selected?.plan_snapshot
  const planTree = useMemo(() => explainTreeData(displayedPlan), [displayedPlan])

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
    setExportStarting(false)
    setExportDownloadState('idle')
    exportCacheRef.current.clear()
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
    if (!autoShowResult || manualTab) return
    if (!selected) return

    const presentation = statementPresentation(selected)
    // Stay on logs until the first result page is ready (incl. 0-row success),
    // matching BigQuery/Databricks job UIs that switch only when rows are viewable.
    if (presentation.kind === 'query') {
      if (query.error) return
      if (query.loading && !page.rows.length && !page.statement_version) return
      if (!page.statement_version && !page.rows.length) return
    } else if (!['success', 'failed', 'cancelled', 'skipped'].includes(selected.status)) {
      return
    }
    changeTab('result', false)
  }, [
    autoShowResult,
    manualTab,
    selected,
    query.error,
    query.loading,
    page.rows.length,
    page.statement_version,
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
    setExportStarting(false)
    setExportDownloadState('idle')
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
    const normalizedHidden = pruneNamedList(layout.hidden, names)
    const normalizedPinned = pruneNamedList(layout.pinned, names).slice(0, MAX_PINNED_RESULT_COLUMNS)
    const semanticTypes = Object.fromEntries(
      fields.map(field => [field.name, field.semantic_type || undefined]),
    )
    return buildQueryTableColumns(fields, {
      order: normalizedOrder,
      widths: normalizedWidths,
      hidden: normalizedHidden,
      pinned: normalizedPinned,
      dataSource,
      serverQuery: true,
      serverFilters,
      semanticTypes,
      loadDistinctValues: run.runId != null && selected
        ? async (column: string) => {
            const response = await adhocRunsApi.sampleStatementColumnValues(
              run.runId!,
              selected.index,
              column,
              {
                limit: 200,
                statement_version: page.statement_version || selected.statement_version,
              },
            )
            return {
              values: (response.values || []).map(valueToFilterKey),
              truncated: response.truncated,
            }
          }
        : undefined,
      onOrderChange: order => persist({
        order,
        widths: normalizedWidths,
        hidden: normalizedHidden,
        pinned: normalizedPinned,
        sourceKeys: names,
      }),
      onWidthChange: (key, width) => persist({
        order: normalizedOrder,
        widths: { ...normalizedWidths, [key]: width },
        hidden: normalizedHidden,
        pinned: normalizedPinned,
        sourceKeys: names,
      }),
    })
  }, [page, dataSource, layout, scopeKey, selected, serverFilters, run.runId])

  const columnNames = useMemo(() => {
    const fields = page.fields.length
      ? page.fields
      : (selected?.fields ?? normalizeQueryColumns(selected?.columns ?? [], selected?.column_types ?? []))
    return fields.map(field => field.name)
  }, [page.fields, selected?.columns, selected?.column_types, selected?.fields])

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
        .filter(value => !(
          value.startsWith('__contains:')
          || value.startsWith('__starts_with:')
          || value.startsWith('__gte:')
          || value.startsWith('__lte:')
        ))
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
      keys
        .filter(value => value.startsWith('__starts_with:'))
        .forEach(value => requestFilters.push({
          column,
          operator: 'starts_with',
          value: value.slice('__starts_with:'.length),
        }))
      keys
        .filter(value => value.startsWith('__gte:'))
        .forEach(value => requestFilters.push({
          column,
          operator: 'gte',
          value: value.slice('__gte:'.length),
        }))
      keys
        .filter(value => value.startsWith('__lte:'))
        .forEach(value => requestFilters.push({
          column,
          operator: 'lte',
          value: value.slice('__lte:'.length),
        }))
    })
    setServerFilters(controlled)
    setFilters(requestFilters)
    setSort(nextSort ? [nextSort] : [])
  }

  const updateColumnLayout = (patch: Partial<Layout>) => {
    if (!selected) return
    const names = columnNames
    const next: Layout = {
      order: resolveResultColumnOrder(layout.order, names, layout.sourceKeys),
      widths: pruneWidths(layout.widths, names),
      hidden: pruneNamedList(layout.hidden, names),
      pinned: pruneNamedList(layout.pinned, names),
      sourceKeys: names,
      ...patch,
    }
    next.hidden = pruneNamedList(next.hidden, names)
    next.pinned = pruneNamedList(next.pinned, names).slice(0, MAX_PINNED_RESULT_COLUMNS)
    if ((next.hidden?.length ?? 0) >= names.length) {
      next.hidden = (next.hidden || []).filter(name => name !== names[0])
    }
    setLayout(next)
    localStorage.setItem(layoutKey(scopeKey, selected), JSON.stringify(next))
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

  const downloadExportJob = async (
    job: InteractiveRunExport,
    automatic = false,
  ): Promise<boolean> => {
    if (!run.runId || !job.download_ready) return false
    setExportDownloadState('downloading')
    try {
      const blob = await adhocRunsApi.downloadExport(run.runId, job.id)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = job.file_name || `run-${run.runId}.${job.format}`
      anchor.style.display = 'none'
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setExportDownloadState('downloaded')
      message.success(automatic
        ? `${job.format.toUpperCase()} 导出完成，已开始下载`
        : '已重新开始下载')
      return true
    } catch (error: any) {
      setExportDownloadState('failed')
      message.warning(automatic
        ? '导出文件已生成，但浏览器未能自动下载，请点击“点击下载”'
        : (error?.response?.data?.detail || error?.message || '下载导出文件失败'))
      return false
    }
  }

  const requestExport = async (format: InteractiveExportFormat) => {
    if (!run.runId || !selected) return
    setExportFormat(format)
    try {
      localStorage.setItem(EXPORT_FORMAT_STORAGE_KEY, format)
    } catch {
      // Remembering the preferred format is optional.
    }
    const statementVersion = page.statement_version || selected.statement_version
    const exportKey = JSON.stringify([
      run.runId,
      selected.index,
      statementVersion,
      format,
      search.trim(),
      filters,
      sort,
    ])
    const reusable = exportCacheRef.current.get(exportKey)
    if (reusable?.status === 'success' && reusable.download_ready) {
      setExportJob(reusable)
      await downloadExportJob(reusable, true)
      return
    }

    exportControllerRef.current?.abort()
    const controller = new AbortController()
    exportControllerRef.current = controller
    const generation = ++exportGenerationRef.current
    setExportStarting(true)
    setExportDownloadState('idle')
    setExportJob(null)
    try {
      let job = await adhocRunsApi.createExport(run.runId, {
        statement_index: selected.index,
        format,
        search: search.trim() || undefined,
        filters: filters.length ? filters : undefined,
        sort: sort.length ? sort : undefined,
        statement_version: statementVersion,
      })
      if (controller.signal.aborted || generation !== exportGenerationRef.current) return
      setExportStarting(false)
      setExportJob(job)
      while (['queued', 'running', 'cancel_requested'].includes(job.status)) {
        await new Promise(resolve => window.setTimeout(resolve, 600))
        if (controller.signal.aborted || generation !== exportGenerationRef.current) return
        job = await adhocRunsApi.getExport(run.runId, job.id, controller.signal)
        if (controller.signal.aborted || generation !== exportGenerationRef.current) return
        setExportJob(job)
      }
      if (job.status === 'success') {
        exportCacheRef.current.set(exportKey, job)
        await downloadExportJob(job, true)
      } else if (job.status === 'failed') message.error(job.error_message || '导出失败')
      else if (job.status === 'cancelled') message.info('导出已取消')
      else if (job.status === 'expired') message.warning('导出文件已过期，请重新导出')
    } catch (error: any) {
      if (error?.code !== 'ERR_CANCELED' && !controller.signal.aborted) {
        message.error(error?.response?.data?.detail || error?.message || '创建导出失败')
      }
    } finally {
      setExportStarting(false)
      if (exportControllerRef.current === controller) exportControllerRef.current = null
    }
  }

  const downloadExport = async () => {
    if (!exportJob) return
    await downloadExportJob(exportJob)
  }

  const cancelExport = async () => {
    if (!run.runId || !exportJob) return
    try {
      exportGenerationRef.current += 1
      exportControllerRef.current?.abort()
      const cancelled = await adhocRunsApi.cancelExport(run.runId, exportJob.id)
      setExportJob(cancelled)
      setExportStarting(false)
      setExportDownloadState('idle')
      message.info('已请求取消导出')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '取消导出失败')
    }
  }

  const createShare = async () => {
    if (!run.runId) return
    setShareAction('create')
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
      setShareAction(action => (action === 'create' ? null : action))
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
    setShareAction('revoke')
    try {
      const revoked = await adhocRunsApi.revokeShare(run.runId, share.id)
      setShare(revoked)
      setShareUrl('')
      message.success('分享链接已撤销')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error?.message || '撤销分享链接失败')
    } finally {
      setShareAction(action => (action === 'revoke' ? null : action))
    }
  }

  const resultBody = selected ? (
    <StatementResultTabs
      statements={run.statements}
      activeKey={selectedKey}
      onChange={setStatementKey}
    >
      {statement => {
        if (!statement) return null
        const presentation = statementPresentation(statement)
        const isQueryGrid = !statement.error && Boolean(statement.columns.length || statement.fields?.length)
        const truncated = Boolean(statement.truncated || page.truncated || (statement.total ?? 0) >= SQL_RESULT_ROW_CAP)
        const previewing = statement.status === 'running'
        const execMs = statement.execution_metrics?.execution_ms
          ?? statement.execution_metrics?.duration_ms
        const activeFilterCols = Object.entries(serverFilters)
          .filter(([, values]) => Array.isArray(values) && values.length > 0)
          .map(([column]) => column)
        const hasQueryMods = Boolean(search.trim() || activeFilterCols.length || sort.length)
        const statusBar = (
          <div className="dw-interactive-run__status-bar" role="status">
            <div className="dw-interactive-run__status-bar-main">
              <Space size={8} wrap split={<span className="dw-interactive-run__status-sep">·</span>}>
                <Tag
                  color={(STATEMENT_META[statement.status] ?? STATEMENT_META.pending).color}
                  style={{ margin: 0 }}
                >
                  {(STATEMENT_META[statement.status] ?? STATEMENT_META.pending).label}
                </Tag>
                <span>{presentation.type}</span>
                <span>耗时 {duration(statement)}</span>
                {execMs != null && <span>执行 {execMs}ms</span>}
                {statement.execution_metrics?.fetch_ms != null && (
                  <span>取数 {statement.execution_metrics.fetch_ms}ms</span>
                )}
                {presentation.kind === 'dml'
                  && statement.affected_rows != null
                  && statement.affected_rows >= 0 && (
                  <span>影响 {statement.affected_rows} 行</span>
                )}
                {presentation.kind === 'query' && (
                  <span>结果 {statement.total} 行</span>
                )}
                <span>{formatBytes(statement.execution_metrics?.result_bytes ?? statement.result_bytes)}</span>
                {previewing && (
                  <Tooltip title="结果仍在物化，当前网格为预览；完成后可导出完整快照">
                    <Tag color="processing" style={{ margin: 0 }}>预览中</Tag>
                  </Tooltip>
                )}
                {!previewing && truncated && (
                  <Tooltip title={`已截断（上限 ${SQL_RESULT_ROW_CAP} 行）。网格为预览，完整数据请导出。`}>
                    <Tag color="orange" style={{ margin: 0 }}>已截断</Tag>
                  </Tooltip>
                )}
                {search.trim() ? <Tag style={{ margin: 0 }}>搜索中</Tag> : null}
                {activeFilterCols.length > 0 ? (
                  <Tooltip title={activeFilterCols.join(', ')}>
                    <Tag style={{ margin: 0 }}>筛选 {activeFilterCols.length} 列</Tag>
                  </Tooltip>
                ) : null}
                {sort.length > 0 ? <Tag style={{ margin: 0 }}>排序 {sort.length}</Tag> : null}
                {hasQueryMods ? (
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0, height: 'auto' }}
                    icon={<ClearOutlined />}
                    onClick={() => {
                      setSearch('')
                      setServerFilters({})
                      setFilters([])
                      setSort([])
                    }}
                  >
                    清除条件
                  </Button>
                ) : null}
                {statement.query_id && (
                  <Tooltip title={statement.query_id}>
                    <span className="dw-interactive-run__status-muted">Query ID {statement.query_id}</span>
                  </Tooltip>
                )}
                {presentation.kind === 'query' && (
                  <Button
                    size="small"
                    type="link"
                    style={{ padding: 0, height: 'auto' }}
                    icon={<ExperimentOutlined />}
                    loading={explainLoading}
                    onClick={() => void requestExplain()}
                  >
                    Explain
                  </Button>
                )}
              </Space>
            </div>
            {isQueryGrid ? (
              <div className="dw-interactive-run__status-bar-actions">
                <Tooltip title={`每页最多 ${RESULT_PAGE_SIZE} 行；表格内滚动浏览当前页，翻页或输入页码向服务端加载`}>
                  <span className="dw-interactive-run__status-muted">
                    {(() => {
                      const start = page.rows.length
                        ? (query.pageNumber - 1) * RESULT_PAGE_SIZE + 1
                        : 0
                      const end = page.rows.length
                        ? (query.pageNumber - 1) * RESULT_PAGE_SIZE + page.rows.length
                        : 0
                      return start
                        ? `${start}–${end} / ${page.total}`
                        : `0 / ${page.total}`
                    })()}
                  </span>
                </Tooltip>
                <span className="dw-interactive-run__page-jumper">
                  <span className="dw-interactive-run__status-muted">第</span>
                  <InputNumber
                    key={query.pageNumber}
                    size="small"
                    min={1}
                    max={query.pageCount}
                    defaultValue={query.pageNumber}
                    disabled={query.loading || page.total < 1}
                    controls={false}
                    style={{ width: 56 }}
                    aria-label="跳转到页码"
                    onPressEnter={event => {
                      query.goToPage(Number((event.target as HTMLInputElement).value))
                    }}
                    onBlur={event => {
                      const raw = (event.target as HTMLInputElement).value
                      if (raw === '' || raw == null) return
                      query.goToPage(Number(raw))
                    }}
                  />
                  <span className="dw-interactive-run__status-muted">/ {query.pageCount} 页</span>
                </span>
                <Button
                  size="small"
                  disabled={query.pageNumber <= 1 || query.loading}
                  onClick={() => query.previous()}
                >
                  上一页
                </Button>
                <Button
                  size="small"
                  disabled={!page.has_more || query.loading}
                  onClick={() => query.next()}
                >
                  下一页
                </Button>
              </div>
            ) : null}
          </div>
        )

        return (
        <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          {isQueryGrid ? (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', position: 'relative' }}>
              {query.loading && !dataSource.length ? (
                <div style={{
                  flex: 1,
                  minHeight: 160,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: '#8c8c8c',
                  gap: 8,
                }}>
                  <Spin size="small" />
                  <span>正在加载结果首页…</span>
                </div>
              ) : (
              <QueryResultPanel
                  dataSource={dataSource}
                  columns={tableColumns}
                  pagination={false}
                  rowNumberOffset={(query.pageNumber - 1) * RESULT_PAGE_SIZE}
                  serverQuery
                  serverSort={sort[0] ?? null}
                  onServerChange={handleServerChange}
                  enableQuickChart
                  onColumnWidthsChange={widths => {
                    updateColumnLayout({
                      widths: pruneWidths({ ...layout.widths, ...widths }, columnNames),
                    })
                  }}
                  chartFields={page.fields.map(field => ({
                    name: field.name,
                    type: field.type,
                    semantic_type: field.semantic_type,
                  }))}
                  toolbar={(
                    <div className="dw-interactive-run__result-tools">
                      <Input.Search
                        allowClear
                        size="small"
                        aria-label="全局搜索查询结果"
                        placeholder="搜索结果"
                        value={search}
                        onChange={event => setSearch(event.target.value)}
                        style={{ width: 180 }}
                      />
                      <Dropdown
                        trigger={['click']}
                        popupRender={() => (
                          <div
                            className="dw-interactive-run__column-menu"
                            onClick={event => event.stopPropagation()}
                          >
                            <div className="dw-interactive-run__column-menu-hint">
                              最多 2 列；也可点击表头排序
                            </div>
                            <Select
                              mode="multiple"
                              aria-label="多列排序"
                              placeholder="选择排序列"
                              value={sort.map(item => JSON.stringify(item))}
                              options={sortOptions}
                              style={{ width: '100%' }}
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
                          </div>
                        )}
                      >
                        <Button size="small" icon={<SortAscendingOutlined />}>
                          {sort.length ? `排序 · ${sort.length}` : '排序'}
                        </Button>
                      </Dropdown>
                      <Dropdown
                        trigger={['click']}
                        popupRender={() => (
                          <div
                            className="dw-interactive-run__column-menu"
                            onClick={event => event.stopPropagation()}
                          >
                            <div className="dw-interactive-run__column-menu-hint">
                              固定列最多 {MAX_PINNED_RESULT_COLUMNS} 个，便于宽表横向对照
                            </div>
                            {columnNames.map(name => {
                              const hidden = new Set(layout.hidden || [])
                              const pinned = new Set(layout.pinned || [])
                              const visible = !hidden.has(name)
                              const isPinned = pinned.has(name)
                              return (
                                <div key={name} className="dw-interactive-run__column-row">
                                  <Checkbox
                                    checked={visible}
                                    onChange={event => {
                                      const nextHidden = new Set(hidden)
                                      if (event.target.checked) nextHidden.delete(name)
                                      else nextHidden.add(name)
                                      updateColumnLayout({ hidden: [...nextHidden] })
                                    }}
                                  >
                                    <span title={name}>{name}</span>
                                  </Checkbox>
                                  <Button
                                    size="small"
                                    type={isPinned ? 'link' : 'text'}
                                    icon={<PushpinOutlined />}
                                    title={isPinned ? '取消固定' : '固定到左侧'}
                                    onClick={() => {
                                      const nextPinned = [...(layout.pinned || [])]
                                      const idx = nextPinned.indexOf(name)
                                      if (idx >= 0) {
                                        nextPinned.splice(idx, 1)
                                      } else if (nextPinned.length >= MAX_PINNED_RESULT_COLUMNS) {
                                        message.warning(`最多固定 ${MAX_PINNED_RESULT_COLUMNS} 列`)
                                        return
                                      } else {
                                        nextPinned.push(name)
                                      }
                                      updateColumnLayout({ pinned: nextPinned })
                                    }}
                                  />
                                </div>
                              )
                            })}
                          </div>
                        )}
                      >
                        <Button size="small" icon={<TableOutlined />}>
                          {((layout.hidden?.length || 0) > 0 || (layout.pinned?.length || 0) > 0)
                            ? `列 · ${(layout.pinned?.length || 0)}钉/${columnNames.length - (layout.hidden?.length || 0)}显`
                            : '列'}
                        </Button>
                      </Dropdown>
                      <Tooltip title={canExportSelected
                        ? '导出当前搜索/筛选/排序下的物化快照'
                        : '运行完成后方可导出'}>
                        <span>
                          <InteractiveExportButton
                            format={exportFormat}
                            job={exportJob}
                            starting={exportStarting}
                            downloadState={exportDownloadState}
                            disabled={!canExportSelected}
                            onExport={format => void requestExport(format)}
                            onCancel={() => void cancelExport()}
                            onDownload={() => void downloadExport()}
                          />
                        </span>
                      </Tooltip>
                    </div>
                  )}
                />
              )}
              {query.loading && dataSource.length > 0 && (
                <div style={{
                  position: 'absolute',
                  top: 8,
                  right: 12,
                  zIndex: 10,
                  padding: '4px 10px',
                  borderRadius: 12,
                  background: 'rgba(255, 255, 255, 0.92)',
                  boxShadow: '0 1px 4px rgba(0, 0, 0, 0.12)',
                  fontSize: 12,
                }}>
                  <Space size={6}><Spin size="small" />加载中</Space>
                </div>
              )}
              {query.error && <div style={{ padding: 12, color: '#ff4d4f' }}>{query.error}</div>}
              {statusBar}
            </div>
          ) : (
            <>
              <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                <StatementExecutionSummary statement={statement} />
              </div>
              {statusBar}
            </>
          )}
        </div>
        )
      }}
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
              <Tooltip title="创建工作空间内可访问的结果分享链接；右侧菜单可改有效期">
                <Dropdown.Button
                  size="small"
                  icon={<LinkOutlined />}
                  loading={shareAction === 'create'}
                  disabled={!run.runId}
                  onClick={() => void createShare()}
                  menu={{
                    selectable: true,
                    selectedKeys: [String(shareTtlHours)],
                    items: [
                      { key: '1', label: '有效期 1 小时' },
                      { key: '24', label: '有效期 1 天' },
                      { key: String(24 * 7), label: '有效期 7 天' },
                      { key: String(24 * 30), label: '有效期 30 天' },
                    ],
                    onClick: ({ key }) => setShareTtlHours(Number(key)),
                  }}
                >
                  {`空间内分享 · ${
                    shareTtlHours === 1 ? '1小时'
                      : shareTtlHours === 24 ? '1天'
                        : shareTtlHours === 24 * 7 ? '7天'
                          : '30天'
                  }`}
                </Dropdown.Button>
              </Tooltip>
            ) : (
              <>
                <Tooltip title={`有效至 ${new Date(share.expires_at).toLocaleString()}`}>
                  <Tag color="green">分享有效</Tag>
                </Tooltip>
                <Button size="small" icon={<CopyOutlined />} onClick={() => void copyShare()}>复制链接</Button>
                <Button size="small" danger loading={shareAction === 'revoke'} onClick={() => void revokeShare()}>撤销</Button>
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

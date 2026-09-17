/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Alert, Button, Empty, Pagination, Space, Spin, Tag, Tooltip, message } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { adhocRunsApi } from '../api'
import type { useInteractiveRun } from '../hooks/useInteractiveRun'
import type {
  InteractiveExportFormat,
  InteractiveRunExport,
  InteractiveRunStatement,
  InteractiveStatementRows,
} from '../types/interactiveRun'
import EditorResultDock, { EditorResultRowBadge } from './EditorResultDock'
import LiveRunPanel from './LiveRunPanel'
import StatementResultTabs from './StatementResultTabs'
import QueryResultPanel from './QueryResultPanel'
import { buildQueryTableColumns, rowsToRecordDataSource } from './QueryResultTable'
import { normalizeQueryColumns } from '../utils/queryColumns'
import { pruneWidths, resolveResultColumnOrder } from '../utils/resultTableMeta'
import { SQL_RESULT_ROW_CAP } from '../utils/sqlResultRowLimit'

type RunController = ReturnType<typeof useInteractiveRun>
type TabKey = 'log' | 'result'
type Layout = { order: string[]; widths: Record<string, number>; sourceKeys?: string[] }
const RESULT_PAGE_SIZE = Math.min(200, SQL_RESULT_ROW_CAP)

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

function layoutKey(scopeKey: string, statement: InteractiveRunStatement): string {
  const fingerprint = statement.columns.join('\x1f')
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
  activeKey,
  onTabChange,
  onClose,
  compact = false,
  autoShowResult = true,
}: {
  run: RunController
  scopeKey: string
  activeKey?: TabKey
  onTabChange?: (key: TabKey) => void
  onClose?: () => void
  compact?: boolean
  autoShowResult?: boolean
}) {
  const [internalTab, setInternalTab] = useState<TabKey>('log')
  const [manualTab, setManualTab] = useState(false)
  const [statementKey, setStatementKey] = useState('0')
  const [page, setPage] = useState<InteractiveStatementRows | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null])
  const [rowsLoading, setRowsLoading] = useState(false)
  const [exportJob, setExportJob] = useState<InteractiveRunExport | null>(null)
  const [layout, setLayout] = useState<Layout>({ order: [], widths: {} })
  const previousRunRef = useRef<number | null>(run.runId)
  const exportGenerationRef = useRef(0)
  const exportControllerRef = useRef<AbortController | null>(null)
  const selected = run.statements.find(item => String(item.index) === statementKey) ?? run.statements[0] ?? null
  const selectedKey = selected ? String(selected.index) : statementKey
  const previousSelectionRef = useRef(selectedKey)
  const currentTab = activeKey ?? internalTab

  const changeTab = (key: TabKey, manual = true) => {
    if (manual) setManualTab(true)
    if (activeKey == null) setInternalTab(key)
    onTabChange?.(key)
  }

  useEffect(() => {
    if (previousRunRef.current === run.runId) return
    previousRunRef.current = run.runId
    setManualTab(false)
    setInternalTab('log')
    setStatementKey('0')
    setPage(null)
    setPageNumber(1)
    setCursorStack([null])
    setExportJob(null)
    exportGenerationRef.current += 1
    exportControllerRef.current?.abort()
    exportControllerRef.current = null
  }, [run.runId])

  useEffect(() => {
    if (!selected || run.statements.some(item => String(item.index) === statementKey)) return
    setStatementKey(String(run.statements.find(item => item.columns.length)?.index ?? run.statements[0]?.index ?? 0))
  }, [run.statements, selected, statementKey])

  useEffect(() => {
    if (!autoShowResult || manualTab || run.status !== 'success' || !run.capabilities.hasResults) return
    changeTab('result', false)
  }, [autoShowResult, manualTab, run.status, run.capabilities.hasResults])

  useEffect(() => {
    if (previousSelectionRef.current === selectedKey) return
    previousSelectionRef.current = selectedKey
    setPage(null)
    setPageNumber(1)
    setCursorStack([null])
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
    if (!run.runId || !selected || !selected.columns.length) return
    const controller = new AbortController()
    setRowsLoading(true)
    adhocRunsApi.statementRows(run.runId, selected.index, {
      cursor: cursorStack[pageNumber - 1],
      limit: RESULT_PAGE_SIZE,
      signal: controller.signal,
    }).then(setPage).catch((error: any) => {
      if (error?.code !== 'ERR_CANCELED') message.error(error?.response?.data?.detail || '加载结果失败')
    }).finally(() => {
      if (!controller.signal.aborted) setRowsLoading(false)
    })
    return () => controller.abort()
  }, [run.runId, run.statementVersion, selected?.index, selected?.columns.length, pageNumber, cursorStack])

  useEffect(() => {
    if (!selected) return
    const key = layoutKey(scopeKey, selected)
    setLayout(readLayout(key))
  }, [scopeKey, selected?.index, selected?.columns.join('\x1f')])

  const dataSource = useMemo(
    () => rowsToRecordDataSource(page?.columns ?? [], page?.rows ?? []),
    [page],
  )
  const tableColumns = useMemo(() => {
    if (!page) return buildQueryTableColumns([])
    const names = page.columns
    const persist = (next: Layout) => {
      setLayout(next)
      if (selected) localStorage.setItem(layoutKey(scopeKey, selected), JSON.stringify(next))
    }
    const normalizedOrder = resolveResultColumnOrder(layout.order, names, layout.sourceKeys)
    const normalizedWidths = pruneWidths(layout.widths, names)
    return buildQueryTableColumns(normalizeQueryColumns(names, page.column_types), {
      order: normalizedOrder,
      widths: normalizedWidths,
      dataSource,
      onOrderChange: order => persist({ order, widths: normalizedWidths, sourceKeys: names }),
      onWidthChange: (key, width) => persist({
        order: normalizedOrder,
        widths: { ...normalizedWidths, [key]: width },
        sourceKeys: names,
      }),
    })
  }, [page, dataSource, layout, scopeKey, selected])

  const requestExport = async (format: InteractiveExportFormat) => {
    if (!run.runId || !selected) return
    exportControllerRef.current?.abort()
    const controller = new AbortController()
    exportControllerRef.current = controller
    const generation = ++exportGenerationRef.current
    setExportJob(null)
    try {
      let job = await adhocRunsApi.createExport(run.runId, selected.index, format)
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
            <span>耗时 {duration(statement)}</span>
            {statement.affected_rows != null && <span>影响 {statement.affected_rows} 行</span>}
            <span>结果 {statement.total} 行</span>
            {statement.truncated && <Tag color="orange">已截断</Tag>}
          </Space>
          {statement.error ? (
            <Alert type="error" showIcon message={`语句 ${statement.index + 1} 执行失败`} description={statement.error} />
          ) : statement.columns.length ? (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              {rowsLoading ? <Spin style={{ margin: 24 }} /> : (
                <QueryResultPanel
                  dataSource={dataSource}
                  columns={tableColumns}
                  pagination={false}
                  toolbar={(
                    <Space wrap>
                      <span>第 {pageNumber} 页 · 本页 {page?.rows.length ?? 0} / 共 {statement.total} 行</span>
                      <Pagination
                        size="small"
                        simple
                        current={pageNumber}
                        pageSize={RESULT_PAGE_SIZE}
                        showSizeChanger={false}
                        total={Math.max(
                          statement.total,
                          (pageNumber - 1) * RESULT_PAGE_SIZE + (page?.rows.length ?? 0) + (page?.has_more ? 1 : 0),
                        )}
                        onChange={next => {
                          if (next < pageNumber && next <= cursorStack.length) {
                            setPageNumber(next)
                            return
                          }
                          if (next !== pageNumber + 1 || !page?.has_more || !page.next_cursor) return
                          setCursorStack(prev => {
                            const known = prev.slice(0, pageNumber)
                            return [...known, page.next_cursor]
                          })
                          setPageNumber(next)
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
                      {exportJob && <Tag>{exportJob.status}</Tag>}
                      {exportJob?.download_ready && (
                        <Button size="small" icon={<DownloadOutlined />} onClick={() => void downloadExport()}>下载</Button>
                      )}
                    </Space>
                  )}
                />
              )}
            </div>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该语句没有结果集" />
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
    <EditorResultDock
      activeKey={currentTab}
      onChange={key => changeTab(key as TabKey)}
      onClose={onClose ?? (() => undefined)}
      closeTitle={onClose ? '关闭结果面板' : '结果面板'}
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
            />
          ),
        },
        {
          key: 'result',
          label: (
            <Tooltip title={run.isActive && run.capabilities.hasResults ? '已有语句结果可查看' : undefined}>
              <span>
                查询结果
                {selected && <EditorResultRowBadge count={selected.total} />}
                {run.isActive && run.capabilities.hasResults && <Tag color="green" style={{ marginLeft: 6 }}>可用</Tag>}
              </span>
            </Tooltip>
          ),
          children: resultBody,
        },
      ]}
    />
  )
}

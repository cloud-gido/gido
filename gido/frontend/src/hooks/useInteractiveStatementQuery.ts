/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { adhocRunsApi } from '../api'
import type {
  InteractiveRowsQueryRequest,
  InteractiveRowsQueryResponse,
} from '../types/interactiveRun'

const EMPTY: InteractiveRowsQueryResponse = {
  fields: [],
  rows: [],
  total: 0,
  source_total: 0,
  next_cursor: null,
  has_more: false,
  statement_version: '',
}

const PAGE_CACHE_LIMIT = 24
const TERMINAL = new Set(['success', 'failed', 'cancelled', 'skipped'])

function versionMismatchCurrent(reason: any): string | null {
  const detail = reason?.response?.data?.detail
  if (detail && typeof detail === 'object' && detail.code === 'statement_version_mismatch') {
    return String(detail.current_statement_version || '')
  }
  if (typeof detail === 'string' && detail.includes('版本')) return ''
  return null
}

function isAbort(reason: any): boolean {
  return reason?.code === 'ERR_CANCELED' || reason?.name === 'AbortError'
}

/**
 * Progressive row query aligned with BigQuery / Databricks / Snowflake job UIs:
 * - While the statement is still running, keep a stable request key so chunk
 *   version bumps do not blank the grid; refresh page 1 in place as chunks land.
 * - Page 1 soft-upgrades on the server; cursor pages are version-strict and the
 *   client silently resets to page 1 when the snapshot changes.
 */
export function useInteractiveStatementQuery(opts: {
  runId: number | null
  statementIndex: number | null
  statementVersion?: string | null
  statementStatus?: string | null
  search?: string
  filters?: InteractiveRowsQueryRequest['filters']
  sort?: InteractiveRowsQueryRequest['sort']
  limit: number
}) {
  const {
    runId,
    statementIndex,
    statementVersion,
    statementStatus,
    search,
    filters,
    sort,
    limit,
  } = opts
  const [data, setData] = useState<InteractiveRowsQueryResponse>(EMPTY)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null])
  const pageCacheRef = useRef(new Map<string, InteractiveRowsQueryResponse>())
  const boundVersionRef = useRef<string | null>(statementVersion || null)
  const progressiveVersionSeenRef = useRef<string | null>(null)
  const dataRef = useRef(data)
  dataRef.current = data

  const progressive = !TERMINAL.has(String(statementStatus || '').toLowerCase())
  const requestKey = useMemo(
    () => JSON.stringify([
      runId,
      statementIndex,
      progressive ? 'progressive' : (statementVersion || ''),
      search || '',
      filters || [],
      sort || [],
      limit,
    ]),
    [runId, statementIndex, progressive, statementVersion, search, filters, sort, limit],
  )
  const [activeRequestKey, setActiveRequestKey] = useState(requestKey)

  useEffect(() => {
    if (statementVersion) boundVersionRef.current = statementVersion
  }, [statementVersion])

  useEffect(() => {
    if (!progressive) {
      progressiveVersionSeenRef.current = null
      return
    }
    if (!statementVersion || progressiveVersionSeenRef.current === statementVersion) return
    progressiveVersionSeenRef.current = statementVersion
    // Bust progressive caches so new chunks refresh page 1 without changing requestKey.
    for (const key of [...pageCacheRef.current.keys()]) {
      if (key.startsWith(`${requestKey}:`)) pageCacheRef.current.delete(key)
    }
  }, [progressive, statementVersion, requestKey])

  useEffect(() => {
    if (activeRequestKey === requestKey) return
    setActiveRequestKey(requestKey)
    setPageNumber(1)
    setCursorStack([null])
    const cached = pageCacheRef.current.get(`${requestKey}:1`)
    if (cached) {
      setData(cached)
      boundVersionRef.current = cached.statement_version || boundVersionRef.current
    } else if (!progressive && !dataRef.current.rows.length) {
      setData(EMPTY)
    }
    // Progressive → terminal keeps the last painted preview until the final page lands.
  }, [activeRequestKey, requestKey, progressive])

  useEffect(() => {
    if (!runId || statementIndex == null || activeRequestKey !== requestKey) return
    const pageCacheKey = `${requestKey}:${pageNumber}`
    const cached = pageCacheRef.current.get(pageCacheKey)
    if (cached) {
      pageCacheRef.current.delete(pageCacheKey)
      pageCacheRef.current.set(pageCacheKey, cached)
      setData(cached)
      boundVersionRef.current = cached.statement_version || boundVersionRef.current
      setLoading(false)
      setError('')
      return
    }

    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      const keepVisible = dataRef.current.rows.length > 0
      if (!keepVisible) setLoading(true)
      setError('')

      const fetchPage = (cursor: string | null, version: string | null | undefined) =>
        adhocRunsApi.queryStatementRows(runId, statementIndex, {
          search: search?.trim() || undefined,
          filters: filters?.length ? filters : undefined,
          sort: sort?.length ? sort : undefined,
          cursor,
          limit,
          statement_version: version || undefined,
        }, controller.signal)

      const remember = (response: InteractiveRowsQueryResponse, key: string) => {
        boundVersionRef.current = response.statement_version || boundVersionRef.current
        pageCacheRef.current.set(key, response)
        while (pageCacheRef.current.size > PAGE_CACHE_LIMIT) {
          const oldest = pageCacheRef.current.keys().next().value
          if (oldest === undefined) break
          pageCacheRef.current.delete(oldest)
        }
      }

      const cursor = cursorStack[pageNumber - 1] ?? null
      // Progressive page-1 always follows the latest event version (server soft-upgrades).
      const versionHint = progressive && pageNumber === 1
        ? (statementVersion || boundVersionRef.current)
        : (boundVersionRef.current || statementVersion)

      fetchPage(cursor, versionHint)
        .catch(async (reason: any) => {
          if (isAbort(reason)) throw reason
          const current = versionMismatchCurrent(reason)
          if (current == null || !cursor) throw reason
          // Soft-reset without the page-2 AbortSignal — setState below remounts the
          // effect and would otherwise cancel the retry mid-flight.
          const response = await adhocRunsApi.queryStatementRows(runId, statementIndex, {
            search: search?.trim() || undefined,
            filters: filters?.length ? filters : undefined,
            sort: sort?.length ? sort : undefined,
            cursor: null,
            limit,
            statement_version: current || undefined,
          })
          if (controller.signal.aborted) return null
          pageCacheRef.current.clear()
          remember(response, `${requestKey}:1`)
          setCursorStack([null])
          setPageNumber(1)
          setData(response)
          return null
        })
        .then(response => {
          if (!response || controller.signal.aborted) return
          remember(response, pageCacheKey)
          setData(response)
          if (response.has_more && response.next_cursor && !progressive) {
            const nextPageCacheKey = `${requestKey}:${pageNumber + 1}`
            if (!pageCacheRef.current.has(nextPageCacheKey)) {
              void fetchPage(response.next_cursor, response.statement_version)
                .then(nextPage => {
                  if (!controller.signal.aborted) remember(nextPage, nextPageCacheKey)
                })
                .catch(() => {
                  // Prefetch is best-effort.
                })
            }
          }
        })
        .catch((reason: any) => {
          if (isAbort(reason)) return
          const detail = reason?.response?.data?.detail
          const message = typeof detail === 'string'
            ? detail
            : (detail?.message || reason?.message || '加载结果失败')
          setError(message)
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false)
        })
    }, search ? 250 : 0)

    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [
    runId,
    statementIndex,
    statementVersion,
    progressive,
    search,
    filters,
    sort,
    limit,
    requestKey,
    activeRequestKey,
    pageNumber,
    cursorStack,
  ])

  const next = useCallback(() => {
    if (!data.has_more || !data.next_cursor) return
    setCursorStack(previous => [...previous.slice(0, pageNumber), data.next_cursor])
    setPageNumber(previous => previous + 1)
  }, [data.has_more, data.next_cursor, pageNumber])

  const previous = useCallback(() => setPageNumber(value => Math.max(1, value - 1)), [])

  return { data, loading, error, pageNumber, next, previous }
}

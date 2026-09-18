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

export function useInteractiveStatementQuery(opts: {
  runId: number | null
  statementIndex: number | null
  statementVersion?: string | null
  search?: string
  filters?: InteractiveRowsQueryRequest['filters']
  sort?: InteractiveRowsQueryRequest['sort']
  limit: number
}) {
  const { runId, statementIndex, statementVersion, search, filters, sort, limit } = opts
  const [data, setData] = useState<InteractiveRowsQueryResponse>(EMPTY)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null])
  const pageCacheRef = useRef(new Map<string, InteractiveRowsQueryResponse>())
  const requestKey = useMemo(
    () => JSON.stringify([runId, statementIndex, statementVersion, search || '', filters || [], sort || [], limit]),
    [runId, statementIndex, statementVersion, search, filters, sort, limit],
  )
  const [activeRequestKey, setActiveRequestKey] = useState(requestKey)

  useEffect(() => {
    if (activeRequestKey === requestKey) return
    setActiveRequestKey(requestKey)
    setPageNumber(1)
    setCursorStack([null])
    setData(pageCacheRef.current.get(`${requestKey}:1`) || EMPTY)
  }, [activeRequestKey, requestKey])

  useEffect(() => {
    if (!runId || statementIndex == null || activeRequestKey !== requestKey) return
    const pageCacheKey = `${requestKey}:${pageNumber}`
    const cached = pageCacheRef.current.get(pageCacheKey)
    if (cached) {
      // Immutable statement_version makes revisiting a page safe and instant.
      pageCacheRef.current.delete(pageCacheKey)
      pageCacheRef.current.set(pageCacheKey, cached)
      setData(cached)
      setLoading(false)
      setError('')
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setLoading(true)
      setError('')
      adhocRunsApi.queryStatementRows(runId, statementIndex, {
        search: search?.trim() || undefined,
        filters: filters?.length ? filters : undefined,
        sort: sort?.length ? sort : undefined,
        cursor: cursorStack[pageNumber - 1] ?? null,
        limit,
        statement_version: statementVersion,
      }, controller.signal)
        .then(response => {
          pageCacheRef.current.set(pageCacheKey, response)
          while (pageCacheRef.current.size > PAGE_CACHE_LIMIT) {
            const oldest = pageCacheRef.current.keys().next().value
            if (oldest === undefined) break
            pageCacheRef.current.delete(oldest)
          }
          setData(response)
          // Prefetch only the adjacent page. It hides normal next-page latency
          // without downloading the full result or growing memory unboundedly.
          if (response.has_more && response.next_cursor) {
            const nextPageCacheKey = `${requestKey}:${pageNumber + 1}`
            if (!pageCacheRef.current.has(nextPageCacheKey)) {
              void adhocRunsApi.queryStatementRows(runId, statementIndex, {
                search: search?.trim() || undefined,
                filters: filters?.length ? filters : undefined,
                sort: sort?.length ? sort : undefined,
                cursor: response.next_cursor,
                limit,
                statement_version: statementVersion,
              }, controller.signal).then(nextPage => {
                if (!controller.signal.aborted) {
                  pageCacheRef.current.set(nextPageCacheKey, nextPage)
                  while (pageCacheRef.current.size > PAGE_CACHE_LIMIT) {
                    const oldest = pageCacheRef.current.keys().next().value
                    if (oldest === undefined) break
                    pageCacheRef.current.delete(oldest)
                  }
                }
              }).catch(() => {
                // Prefetch is an optimization; foreground navigation owns errors.
              })
            }
          }
        })
        .catch((reason: any) => {
          if (reason?.code !== 'ERR_CANCELED') {
            setError(reason?.response?.data?.detail || reason?.message || '加载结果失败')
          }
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

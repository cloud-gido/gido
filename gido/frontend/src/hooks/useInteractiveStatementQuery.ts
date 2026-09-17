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
  const requestKey = useMemo(
    () => JSON.stringify([runId, statementIndex, statementVersion, search || '', filters || [], sort || [], limit]),
    [runId, statementIndex, statementVersion, search, filters, sort, limit],
  )
  const previousKey = useRef(requestKey)

  useEffect(() => {
    if (previousKey.current === requestKey) return
    previousKey.current = requestKey
    setPageNumber(1)
    setCursorStack([null])
    setData(EMPTY)
  }, [requestKey])

  useEffect(() => {
    if (!runId || statementIndex == null) return
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
        .then(setData)
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

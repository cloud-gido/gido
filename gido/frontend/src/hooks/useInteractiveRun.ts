/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { adhocRunsApi } from '../api'
import type {
  InteractiveRunCapabilities,
  InteractiveRunState,
  InteractiveRunStatus,
} from '../types/interactiveRun'

export type { InteractiveRunStatus, InteractiveRunState } from '../types/interactiveRun'

const TERMINAL = new Set<InteractiveRunStatus>(['success', 'failed', 'cancelled', 'timed_out'])
const ACTIVE = new Set<InteractiveRunStatus>(['starting', 'queued', 'running', 'cancel_requested'])
const SCOPE_CACHE_LIMIT = 50
const scopeCache = new Map<string, InteractiveRunState>()
const scopeCursors = new Map<string, { runId: number; seq: number }>()

const emptyState = (): InteractiveRunState => ({
  runId: null,
  status: 'idle',
  version: -1,
  log: '',
  error: '',
  statements: [],
  statementVersion: null,
})

function cacheScopeState(scopeKey: string, state: InteractiveRunState) {
  scopeCache.delete(scopeKey)
  scopeCache.set(scopeKey, state)
  while (scopeCache.size > SCOPE_CACHE_LIMIT) {
    const oldest = scopeCache.keys().next().value as string | undefined
    if (oldest == null) break
    scopeCache.delete(oldest)
    scopeCursors.delete(oldest)
  }
}

function acceptsStatus(
  previous: InteractiveRunState,
  version: number,
  incoming: InteractiveRunStatus,
): boolean {
  if (version < previous.version) return false
  if (version > previous.version) return true
  if (previous.status === 'cancel_requested' && ['queued', 'running'].includes(incoming)) return false
  if (TERMINAL.has(previous.status) && !TERMINAL.has(incoming)) return false
  return true
}

export function useInteractiveRun(opts: {
  workspaceId?: number | null
  nodeId?: number | null
  source: 'studio' | 'probe'
  autoRecover?: boolean
  recoveryKey?: string | null
}) {
  const { workspaceId, nodeId, source, autoRecover = true, recoveryKey } = opts
  const scopeKey = `${workspaceId ?? 'none'}:${source}:${nodeId ?? recoveryKey ?? 'none'}`
  const [state, setState] = useState<InteractiveRunState>(() => scopeCache.get(scopeKey) ?? emptyState())
  const stateScopeRef = useRef(scopeKey)
  const stateRef = useRef(state)
  stateRef.current = state
  const cursorRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const generationRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)
  const retryRef = useRef(0)

  const stopTimer = useCallback(() => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current)
    timerRef.current = null
    controllerRef.current?.abort()
    controllerRef.current = null
  }, [])

  const poll = useCallback(async (runId: number, generation: number) => {
    if (generation !== generationRef.current) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      let mustPoll = true
      while (mustPoll && generation === generationRef.current) {
        const events = await adhocRunsApi.events(runId, {
          after_log_seq: cursorRef.current,
          statement_version: stateRef.current.statementVersion ?? undefined,
          log_limit: 500,
          signal: controller.signal,
        })
        if (generation !== generationRef.current) return
        retryRef.current = 0
        cursorRef.current = Math.max(cursorRef.current, Number(events.logs.next_seq || 0))
        scopeCursors.set(scopeKey, { runId, seq: cursorRef.current })
        setState(prev => {
          const statusAccepted = acceptsStatus(prev, events.version, events.status)
          const next: InteractiveRunState = {
            ...prev,
            runId,
            version: Math.max(prev.version, events.version),
            status: statusAccepted ? events.status : prev.status,
            log: prev.log + events.logs.chunks.map(item => item.content || '').join(''),
            statements: events.statements_changed ? events.statements : prev.statements,
            statementVersion: events.statement_version,
          }
          stateRef.current = next
          cacheScopeState(scopeKey, next)
          return next
        })
        // A terminal transition is not complete until every queued log page is drained.
        mustPoll = events.logs.has_more
        if (!mustPoll) {
          if (events.poll_required) {
            const delay = document.hidden ? 3000 : 1000
            timerRef.current = window.setTimeout(() => void poll(runId, generation), delay)
          }
          return
        }
      }
    } catch (error: any) {
      if (generation === generationRef.current && error?.code !== 'ERR_CANCELED') {
        retryRef.current += 1
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(retryRef.current, 5))
        timerRef.current = window.setTimeout(() => void poll(runId, generation), document.hidden ? Math.max(3000, delay) : delay)
      }
    }
  }, [scopeKey])

  const attach = useCallback((runId: number) => {
    stopTimer()
    generationRef.current += 1
    const generation = generationRef.current
    cursorRef.current = scopeCursors.get(scopeKey)?.runId === runId
      ? scopeCursors.get(scopeKey)!.seq
      : 0
    retryRef.current = 0
    const next = stateRef.current.runId === runId
      ? stateRef.current
      : { ...emptyState(), runId, status: 'queued' as const }
    stateRef.current = next
    cacheScopeState(scopeKey, next)
    scopeCursors.set(scopeKey, { runId, seq: cursorRef.current })
    setState(next)
    void poll(runId, generation)
  }, [poll, scopeKey, stopTimer])

  const start = useCallback(async (submitter: () => Promise<any>) => {
    stopTimer()
    generationRef.current += 1
    const generation = generationRef.current
    const starting = { ...emptyState(), status: 'starting' as const }
    stateRef.current = starting
    cacheScopeState(scopeKey, starting)
    setState(starting)
    try {
      const response: any = await submitter()
      if (generation !== generationRef.current) return response
      attach(Number(response.run_id))
      return response
    } catch (error: any) {
      if (generation === generationRef.current) {
        const failed = {
          ...emptyState(),
          status: 'failed' as const,
          error: error?.response?.data?.detail || error?.message || '提交运行失败',
        }
        stateRef.current = failed
        cacheScopeState(scopeKey, failed)
        setState(failed)
      }
      throw error
    }
  }, [attach, scopeKey, stopTimer])

  const cancel = useCallback(async () => {
    const runId = stateRef.current.runId
    if (!runId || TERMINAL.has(stateRef.current.status)) return
    const response = await adhocRunsApi.cancel(runId)
    setState(prev => {
      if (prev.runId !== runId || TERMINAL.has(prev.status)) return prev
      const next = { ...prev, status: response.status }
      stateRef.current = next
      cacheScopeState(scopeKey, next)
      return next
    })
  }, [scopeKey])

  useEffect(() => {
    stopTimer()
    generationRef.current += 1
    const cleanup = () => {
      generationRef.current += 1
      stopTimer()
    }
    cursorRef.current = 0
    retryRef.current = 0
    const cached = scopeCache.get(scopeKey) ?? emptyState()
    stateScopeRef.current = scopeKey
    stateRef.current = cached
    setState(cached)
    if (cached.runId != null) {
      attach(cached.runId)
    }
    if (!autoRecover || !workspaceId || (source === 'studio' && !nodeId)) return cleanup
    const generation = generationRef.current
    adhocRunsApi
      .active(workspaceId, {
        source,
        ...(nodeId ? { node_id: nodeId } : {}),
        ...(source === 'probe' && recoveryKey ? { object_name: `probe:${recoveryKey}` } : {}),
      })
      .then((run: any) => {
        if (generation === generationRef.current && run?.id) attach(Number(run.id))
      })
      .catch(() => undefined)
    return cleanup
  }, [workspaceId, nodeId, source, autoRecover, recoveryKey, scopeKey, attach, stopTimer])

  useEffect(() => {
    const refresh = () => {
      if (document.hidden || !stateRef.current.runId) return
      stopTimer()
      void poll(stateRef.current.runId, generationRef.current)
    }
    document.addEventListener('visibilitychange', refresh)
    return () => document.removeEventListener('visibilitychange', refresh)
  }, [poll, stopTimer])

  const visibleState = stateScopeRef.current === scopeKey
    ? state
    : scopeCache.get(scopeKey) ?? emptyState()
  const capabilities = useMemo<InteractiveRunCapabilities>(() => ({
    hasStatements: visibleState.statements.length > 0,
    hasResults: visibleState.statements.some(item => item.columns.length > 0),
    canExport: visibleState.statements.some(item =>
      ['success', 'failed', 'skipped', 'cancelled'].includes(item.status) && item.columns.length > 0),
    supportsMultipleStatements: visibleState.statements.length > 1,
  }), [visibleState.statements])

  return {
    ...visibleState,
    // Kept during migration for callers that used the legacy preview contract.
    result: null,
    capabilities,
    isActive: ACTIVE.has(visibleState.status),
    start,
    attach,
    cancel,
  }
}

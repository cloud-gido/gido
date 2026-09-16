import { useCallback, useEffect, useRef, useState } from 'react'
import { adhocRunsApi } from '../api'

export type InteractiveRunStatus =
  | 'idle'
  | 'starting'
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'success'
  | 'failed'
  | 'cancelled'
  | 'timed_out'

const TERMINAL = new Set(['success', 'failed', 'cancelled', 'timed_out'])

export interface InteractiveRunState {
  runId: number | null
  status: InteractiveRunStatus
  log: string
  result: any
  error: string
  startedAt?: string
  heartbeatAt?: string
}

export function useInteractiveRun(opts: {
  workspaceId?: number | null
  nodeId?: number | null
  source: 'studio' | 'probe'
  autoRecover?: boolean
  recoveryKey?: string | null
}) {
  const { workspaceId, nodeId, source, autoRecover = true, recoveryKey } = opts
  const [state, setState] = useState<InteractiveRunState>({
    runId: null,
    status: 'idle',
    log: '',
    result: null,
    error: '',
  })
  const cursorRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const generationRef = useRef(0)
  const terminalDrainRef = useRef(false)

  const stopTimer = useCallback(() => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current)
    timerRef.current = null
  }, [])

  const poll = useCallback(async (runId: number, generation: number) => {
    if (generation !== generationRef.current) return
    try {
      const [run, logs]: any[] = await Promise.all([
        adhocRunsApi.get(runId),
        adhocRunsApi.logs(runId, cursorRef.current),
      ])
      if (generation !== generationRef.current) return
      const chunks = logs?.chunks || []
      cursorRef.current = Number(logs?.next_seq || cursorRef.current)
      setState(prev => ({
        ...prev,
        runId,
        status: run?.status || prev.status,
        log: prev.log + chunks.map((item: any) => item.content || '').join(''),
        result: run?.result_preview ?? prev.result,
        error: run?.error_message || '',
        startedAt: run?.started_at,
        heartbeatAt: run?.heartbeat_at,
      }))
      if (!TERMINAL.has(String(run?.status))) {
        timerRef.current = window.setTimeout(() => void poll(runId, generation), document.hidden ? 3000 : 1000)
      } else if (!terminalDrainRef.current) {
        terminalDrainRef.current = true
        timerRef.current = window.setTimeout(() => void poll(runId, generation), 250)
      }
    } catch {
      if (generation === generationRef.current) {
        timerRef.current = window.setTimeout(() => void poll(runId, generation), 3000)
      }
    }
  }, [])

  const attach = useCallback((runId: number) => {
    stopTimer()
    generationRef.current += 1
    const generation = generationRef.current
    cursorRef.current = 0
    terminalDrainRef.current = false
    setState({ runId, status: 'queued', log: '', result: null, error: '' })
    void poll(runId, generation)
  }, [poll, stopTimer])

  const start = useCallback(async (submitter: () => Promise<any>) => {
    stopTimer()
    generationRef.current += 1
    setState({ runId: null, status: 'starting', log: '', result: null, error: '' })
    try {
      const response: any = await submitter()
      attach(Number(response.run_id))
      return response
    } catch (error: any) {
      setState({
        runId: null,
        status: 'failed',
        log: '',
        result: null,
        error: error?.response?.data?.detail || error?.message || '提交运行失败',
      })
      throw error
    }
  }, [attach, stopTimer])

  const cancel = useCallback(async () => {
    if (!state.runId || TERMINAL.has(state.status)) return
    await adhocRunsApi.cancel(state.runId)
    setState(prev => ({ ...prev, status: 'cancel_requested' }))
  }, [state.runId, state.status])

  useEffect(() => {
    stopTimer()
    generationRef.current += 1
    cursorRef.current = 0
    terminalDrainRef.current = false
    setState({ runId: null, status: 'idle', log: '', result: null, error: '' })
    if (!autoRecover || !workspaceId || (source === 'studio' && !nodeId)) return
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
    return stopTimer
  }, [workspaceId, nodeId, source, autoRecover, recoveryKey, attach, stopTimer])

  return {
    ...state,
    isActive: ['starting', 'queued', 'running', 'cancel_requested'].includes(state.status),
    start,
    attach,
    cancel,
  }
}

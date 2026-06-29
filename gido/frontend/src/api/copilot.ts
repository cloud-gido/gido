/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import request from './request'

export type CopilotStatus = {
  configured: boolean
  model: string
  base_url: string
  message: string | null
}

export type CopilotQueryResult = {
  sql?: string
  columns?: string[]
  rows?: unknown[][]
  total?: number
  truncated?: boolean
  datasource_id?: number
  datasource_name?: string
  error?: string
}

export type CopilotChatResponse = {
  session_id: string
  message: string
  sql?: string | null
  query_result?: CopilotQueryResult | null
  tool_trace?: unknown[]
}

export type CopilotStreamEvent =
  | { event: 'status'; data: { phase?: string } }
  | { event: 'delta'; data: { content?: string } }
  | { event: 'done'; data: CopilotChatResponse }
  | { event: 'error'; data: { detail?: string } }

const apiOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''
const chatUrl = apiOrigin ? `${apiOrigin}/api/copilot/chat` : '/api/copilot/chat'

export const copilotApi = {
  status: (): Promise<CopilotStatus> => request.get('/copilot/status'),
  listSessions: (workspaceId?: number) =>
    request.get('/copilot/sessions', { params: workspaceId ? { workspace_id: workspaceId } : {} }),
  getSession: (sessionId: string) => request.get(`/copilot/sessions/${sessionId}`),
  deleteSession: (sessionId: string) => request.delete(`/copilot/sessions/${sessionId}`),
  chat: (data: {
    workspace_id: number
    message: string
    session_id?: string
    datasource_id?: number
  }): Promise<CopilotChatResponse> => request.post('/copilot/chat', { ...data, stream: false }),
}

export async function copilotChatStream(
  body: {
    workspace_id: number
    message: string
    session_id?: string
    datasource_id?: number
  },
  onEvent: (ev: CopilotStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('token')
  const res = await fetch(chatUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ ...body, stream: true }),
    signal,
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const j = await res.json()
      detail = j.detail || detail
    } catch {
      /* ignore */
    }
    onEvent({ event: 'error', data: { detail: String(detail) } })
    return
  }
  const reader = res.body?.getReader()
  if (!reader) {
    onEvent({ event: 'error', data: { detail: '无法读取流式响应' } })
    return
  }
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const block of parts) {
      const lines = block.split('\n')
      let event = 'message'
      let dataStr = ''
      for (const line of lines) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) dataStr = line.slice(5).trim()
      }
      if (!dataStr) continue
      try {
        const data = JSON.parse(dataStr)
        onEvent({ event, data } as CopilotStreamEvent)
      } catch {
        /* ignore malformed chunk */
      }
    }
  }
}

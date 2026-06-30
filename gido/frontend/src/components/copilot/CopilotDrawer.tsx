/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Drawer, Input, Select, Space, Spin, Table, Typography, message } from 'antd'
import { CopyOutlined, CommentOutlined, ExperimentOutlined, SendOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { copilotApi, copilotChatStream, type CopilotQueryResult, type CopilotStatus } from '../../api/copilot'
import { datasourceApi } from '../../api'
import { R } from '../../routes'
import './copilot.css'

const { Text } = Typography

type ChatItem = {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  sql?: string | null
  queryResult?: CopilotQueryResult | null
}

type Props = {
  open: boolean
  onClose: () => void
  workspaceId?: number
}

const PROBE_TYPES = new Set(['mysql', 'doris', 'postgresql'])

export default function CopilotDrawer({ open, onClose, workspaceId }: Props) {
  const navigate = useNavigate()
  const [status, setStatus] = useState<CopilotStatus | null>(null)
  const [datasources, setDatasources] = useState<any[]>([])
  const [datasourceId, setDatasourceId] = useState<number | undefined>()
  const [items, setItems] = useState<ChatItem[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>()
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const scrollBottom = useCallback(() => {
    requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }))
  }, [])

  useEffect(() => {
    if (!open) return
    copilotApi.status(workspaceId).then(setStatus).catch(() => {
      setStatus({ configured: false, model: '', base_url: '', message: '无法获取 Copilot 状态' })
    })
  }, [open, workspaceId])

  useEffect(() => {
    if (!open || !workspaceId) {
      setDatasources([])
      setDatasourceId(undefined)
      return
    }
    datasourceApi.list(workspaceId).then((list: any) => {
      const raw = Array.isArray(list) ? list : []
      const probeDs = raw.filter((d: any) => PROBE_TYPES.has(String(d.ds_type || '').toLowerCase()) && d.is_active !== false)
      setDatasources(probeDs)
      setDatasourceId(prev => {
        if (prev && probeDs.some(d => d.id === prev)) return prev
        const doris = probeDs.find(d => String(d.ds_type).toLowerCase() === 'doris')
        return (doris || probeDs[0])?.id
      })
    }).catch(() => setDatasources([]))
  }, [open, workspaceId])

  useEffect(() => {
    if (open) scrollBottom()
  }, [items, open, scrollBottom])

  const appendAssistant = (content: string, sql?: string | null, queryResult?: CopilotQueryResult | null) => {
    setItems(prev => [...prev, {
      id: `a-${Date.now()}`,
      role: 'assistant',
      content,
      sql,
      queryResult,
    }])
  }

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return
    if (!workspaceId) {
      message.warning('请先选择工作空间')
      return
    }
    if (!status?.configured) {
      message.warning(status?.message || 'Copilot 尚未配置 LLM')
      return
    }

    setInput('')
    setItems(prev => [...prev, { id: `u-${Date.now()}`, role: 'user', content: text }])
    setLoading(true)

    const assistantId = `a-stream-${Date.now()}`
    setItems(prev => [...prev, { id: assistantId, role: 'assistant', content: '' }])

    abortRef.current?.abort()
    abortRef.current = new AbortController()

    let streamed = ''
    try {
      await copilotChatStream(
        {
          workspace_id: workspaceId,
          message: text,
          session_id: sessionId,
          datasource_id: datasourceId,
        },
        ev => {
          if (ev.event === 'delta') {
            streamed += ev.data.content || ''
            setItems(prev => prev.map(it => (it.id === assistantId ? { ...it, content: streamed } : it)))
          }
          if (ev.event === 'done') {
            const d = ev.data
            if (d.session_id) setSessionId(d.session_id)
            setItems(prev => prev.map(it => (
              it.id === assistantId
                ? { ...it, content: d.message || streamed, sql: d.sql, queryResult: d.query_result }
                : it
            )))
          }
          if (ev.event === 'error') {
            setItems(prev => prev.filter(it => it.id !== assistantId))
            appendAssistant(`出错了：${ev.data.detail || '未知错误'}`)
          }
        },
        abortRef.current.signal,
      )
    } catch (e: any) {
      if (e?.name !== 'AbortError') {
        setItems(prev => prev.filter(it => it.id !== assistantId))
        appendAssistant(`请求失败：${e?.message || '网络错误'}`)
      }
    } finally {
      setLoading(false)
      scrollBottom()
    }
  }

  const copySql = (sql: string) => {
    navigator.clipboard.writeText(sql).then(() => message.success('SQL 已复制'))
  }

  const openInProbe = (sql: string) => {
    sessionStorage.setItem('gido_copilot_sql', sql)
    navigate(R.batch.probe)
    onClose()
  }

  const renderResultTable = (qr: CopilotQueryResult) => {
    const cols = (qr.columns || []).map((c, i) => ({
      title: c,
      dataIndex: i,
      key: `${c}-${i}`,
      ellipsis: true,
    }))
    const data = (qr.rows || []).map((row, ri) => {
      const rec: Record<string, unknown> = { key: ri }
      row.forEach((cell, ci) => { rec[ci] = cell })
      return rec
    })
    if (!cols.length) return null
    return (
      <div className="copilot-result-table">
        <Table size="small" columns={cols} dataSource={data} pagination={{ pageSize: 10, size: 'small' }} scroll={{ x: true }} />
      </div>
    )
  }

  return (
    <Drawer
      title={(
        <Space>
          <CommentOutlined style={{ color: '#6366f1', fontSize: 15 }} />
          <span>玑渡 Copilot</span>
          {status?.configured && (
            <span className="copilot-model-tag">{status.model}</span>
          )}
        </Space>
      )}
      placement="right"
      width={440}
      open={open}
      onClose={onClose}
      destroyOnClose={false}
      className="copilot-drawer"
    >
      <div className="copilot-drawer-body">
        {!status?.configured && (
          <div className="copilot-config-hint">
            {status?.message || '请在「空间设置 → Copilot」或「系统管理 → 平台集成」中配置 LLM API Key。'}
          </div>
        )}

        <div className="copilot-toolbar">
          <Select
            style={{ flex: 1, minWidth: 160 }}
            placeholder="探查数据源"
            value={datasourceId}
            onChange={setDatasourceId}
            options={datasources.map(d => ({
              label: `${d.name} (${d.ds_type})`,
              value: d.id,
            }))}
            disabled={!datasources.length}
          />
          <Button
            size="small"
            onClick={() => {
              setItems([])
              setSessionId(undefined)
            }}
          >
            新对话
          </Button>
        </div>

        <div className="copilot-messages">
          {items.length === 0 && (
            <div className="copilot-msg copilot-msg--system">
              你好，我可以帮你查表、解释字段、执行只读 SQL。请先选择数据源，然后输入问题。
            </div>
          )}
          {items.map(item => (
            <div key={item.id} className={`copilot-msg copilot-msg--${item.role}`}>
              {item.role === 'assistant' && !item.content && loading ? <Spin size="small" /> : item.content}
              {item.sql && (
                <div className="copilot-sql-block">{item.sql}</div>
              )}
              {item.sql && (
                <Space size="small" style={{ marginTop: 6 }}>
                  <Button type="link" size="small" icon={<CopyOutlined />} onClick={() => copySql(item.sql!)}>
                    复制 SQL
                  </Button>
                  <Button type="link" size="small" icon={<ExperimentOutlined />} onClick={() => openInProbe(item.sql!)}>
                    在探查中打开
                  </Button>
                </Space>
              )}
              {item.queryResult && renderResultTable(item.queryResult)}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <div className="copilot-input-area">
          <Input.TextArea
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="例如：有哪些订单相关的表？昨天订单量是多少？"
            autoSize={{ minRows: 2, maxRows: 5 }}
            onPressEnter={e => {
              if (!e.shiftKey) {
                e.preventDefault()
                handleSend()
              }
            }}
            disabled={loading}
          />
          <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text type="secondary" style={{ fontSize: 11 }}>Enter 发送 · Shift+Enter 换行</Text>
            <Button type="primary" icon={<SendOutlined />} loading={loading} onClick={handleSend}>
              发送
            </Button>
          </div>
        </div>
      </div>
    </Drawer>
  )
}

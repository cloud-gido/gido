/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Table, Button, Space, Tag, message, Popconfirm, Typography, Alert, Drawer, Tooltip,
} from 'antd'
import { ReloadOutlined, DeleteOutlined, LinkOutlined, BugOutlined, StopOutlined } from '@ant-design/icons'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { can, P } from '../perm'
import { R } from '../routes'
import { Link } from 'react-router-dom'
import { formatInTimeZone } from '../utils/datetime'

const { Paragraph, Text } = Typography

function flinkStatusDisplay(fs: string | undefined) {
  if (!fs) return <Text type="secondary">—</Text>
  const color: Record<string, string> = {
    APPLICATION_PENDING_JOB_ID: 'orange',
    NOT_FOUND_ON_JM: 'volcano',
    UNKNOWN: 'default',
    RUNNING: 'processing',
    INITIALIZING: 'processing',
    CREATED: 'default',
    FINISHED: 'success',
    FAILED: 'error',
    CANCELED: 'warning',
    CANCELLED: 'warning',
    CANCELLING: 'warning',
  }
  return <Tag color={color[fs] || 'blue'}>{fs}</Tag>
}

/** Session：有 jobId 才轮询 JM。K8s Application：cluster 已创建即可轮询（回填 jobId 前也能拿到 APPLICATION_PENDING_JOB_ID） */
function jobNeedsFlinkStatusPoll(j: any) {
  if (j.flink_job_id) return true
  const mode = (j.flink_sql_submit_mode || 'session').toString().toLowerCase()
  return mode === 'kubernetes_application' && Boolean(j.flink_application_cluster_id)
}

export default function StreamMonitorPage() {
  const { currentWorkspace, user } = useAppStore()
  const wsId = currentWorkspace?.id
  const displayTz = currentWorkspace?.timezone || 'Asia/Shanghai'
  const [jobs, setJobs] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [flinkMap, setFlinkMap] = useState<Record<number, { flink_status?: string; status?: string }>>({})
  const [diagOpen, setDiagOpen] = useState(false)
  const [diagRow, setDiagRow] = useState<any | null>(null)
  const [diagExceptions, setDiagExceptions] = useState<any>(null)
  const [diagSync, setDiagSync] = useState<any>(null)

  const jobsRef = useRef<any[]>([])

  useEffect(() => {
    jobsRef.current = jobs
  }, [jobs])

  const loadJobs = useCallback(async (showSpinner = true) => {
    if (!wsId) return
    if (showSpinner) setLoading(true)
    try {
      const list: any = await streamingApi.listJobs(wsId)
      setJobs(list)
    } finally {
      if (showSpinner) setLoading(false)
    }
  }, [wsId])

  const syncAll = useCallback(async () => {
    if (!wsId || jobs.length === 0) return
    const next: Record<number, { flink_status?: string; status?: string }> = {}
    for (const j of jobs) {
      if (!jobNeedsFlinkStatusPoll(j)) continue
      try {
        const s: any = await streamingApi.getStatus(j.id)
        next[j.id] = { flink_status: s.flink_status, status: s.status }
      } catch {
        next[j.id] = { flink_status: 'UNKNOWN' }
      }
    }
    setFlinkMap(prev => ({ ...prev, ...next }))
    message.success('已同步 Flink 状态')
    await loadJobs()
  }, [wsId, jobs, loadJobs])

  useEffect(() => { loadJobs() }, [loadJobs])

  const openDiagnostics = async (row: any) => {
    setDiagRow(row)
    setDiagExceptions(null)
    setDiagSync(null)
    setDiagOpen(true)
    try {
      const s: any = await streamingApi.getStatus(row.id)
      setDiagSync(s)
    } catch (e: any) {
      setDiagSync({ error: e?.response?.data?.detail || e.message })
    }
    if (!row.flink_job_id) return
    try {
      const ex: any = await streamingApi.getExceptions(row.id)
      setDiagExceptions(ex)
    } catch (e: any) {
      setDiagExceptions({ error: e?.response?.data?.detail || e.message })
    }
  }

  const canRemove = can(user, P.GIDO_STREAM_WRITE, currentWorkspace)
  const canRun = can(user, P.GIDO_STREAM_RUN, currentWorkspace)

  const handleRemove = async (row: any) => {
    try {
      await streamingApi.deleteJob(row.id)
      message.success('已从平台删除该任务记录')
      setFlinkMap(prev => {
        const n = { ...prev }
        delete n[row.id]
        return n
      })
      await loadJobs()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '删除失败')
    }
  }

  const handleStop = async (row: any) => {
    try {
      await streamingApi.cancelJob(row.id)
      message.success('已请求停止')
      await loadJobs()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '停止失败')
    }
  }

  /** 轮询 Flink 回填库内 status；同时刷新「平台状态」与 Flink 状态列（避免 JM 侧已停止而平台仍为 running） */
  useEffect(() => {
    let alive = true
    const poll = async () => {
      if (!wsId || !alive) return
      const list = jobsRef.current
      const withFlink = list.filter(jobNeedsFlinkStatusPoll)
      if (withFlink.length === 0) return
      const nextMap: Record<number, { flink_status?: string; status?: string }> = {}
      await Promise.all(
        withFlink.map(async j => {
          try {
            const s: any = await streamingApi.getStatus(j.id)
            nextMap[j.id] = { flink_status: s.flink_status, status: s.status }
          } catch {
            nextMap[j.id] = { flink_status: 'UNKNOWN' }
          }
        }),
      )
      if (!alive) return
      setFlinkMap(prev => ({ ...prev, ...nextMap }))
      await loadJobs(false)
    }
    poll()
    const t = window.setInterval(poll, 6500)
    return () => {
      alive = false
      window.clearInterval(t)
    }
  }, [wsId, loadJobs])

  const statusColor: Record<string, string> = {
    draft: 'default', running: 'processing', finished: 'success', failed: 'error', cancelled: 'warning',
  }

  const columns = [
    { title: '作业名', dataIndex: 'name', key: 'name', width: 180, ellipsis: true },
    { title: '类型', dataIndex: 'job_type', key: 'job_type', width: 64, render: (t: string) => <Tag>{t}</Tag> },
    {
      title: 'SQL 部署',
      key: 'deploy',
      width: 100,
      render: (_: unknown, row: any) => {
        if (row.job_type !== 'SQL') return <Text type="secondary">—</Text>
        const m = (row.flink_sql_submit_mode || 'session').toString().toLowerCase()
        return (
          <Tag color={m === 'kubernetes_application' ? 'purple' : 'geekblue'}>
            {m === 'kubernetes_application' ? 'Application' : 'Session'}
          </Tag>
        )
      },
    },
    {
      title: '平台状态', dataIndex: 'status', key: 'status', width: 88,
      render: (s: string) => <Tag color={statusColor[s] || 'default'}>{s}</Tag>,
    },
    {
      title: 'Flink 状态',
      key: 'fs',
      width: 128,
      render: (_: any, row: any) => flinkStatusDisplay(flinkMap[row.id]?.flink_status),
    },
    {
      title: 'clusterID',
      key: 'cid',
      width: 120,
      ellipsis: true,
      render: (_: unknown, row: any) =>
        row.flink_application_cluster_id ? (
          <Typography.Paragraph copyable={{ text: row.flink_application_cluster_id }} style={{ marginBottom: 0, fontSize: 12 }}>
            {row.flink_application_cluster_id}
          </Typography.Paragraph>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '最近提交',
      key: 'lsub',
      width: 132,
      render: (_: unknown, row: any) => (
        <div style={{ fontSize: 12, lineHeight: 1.35 }}>
          <div>{formatInTimeZone(row.last_submitted_at, displayTz)}</div>
          <Text type="secondary">{row.last_submitted_by_username || '—'}</Text>
        </div>
      ),
    },
    {
      title: 'Flink 控制台',
      key: 'fc',
      width: 108,
      render: (_: any, row: any) =>
        row.flink_console_url ? (
          <a href={row.flink_console_url} target="_blank" rel="noreferrer">
            <LinkOutlined /> 打开
          </a>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
    {
      title: '启动失败 / 诊断',
      key: 'diag',
      width: 118,
      render: (_: any, row: any) => (
        <Button
          type="link"
          size="small"
          icon={<BugOutlined />}
          onClick={() => openDiagnostics(row)}
        >
          {row.last_submit_error ? '查看' : '运行时异常'}
        </Button>
      ),
    },
    { title: 'Flink Job ID', dataIndex: 'flink_job_id', key: 'flink_job_id', ellipsis: true, width: 140 },
    { title: '并行度', dataIndex: 'parallelism', key: 'parallelism', width: 64 },
    ...(canRun
      ? [{
          title: '停止',
          key: 'stop',
          width: 76,
          render: (_: unknown, row: any) => {
            if (row.flink_job_id) {
              return (
                <Popconfirm title="在 Flink 上停止该作业？" onConfirm={() => handleStop(row)}>
                  <Button type="link" size="small" danger icon={<StopOutlined />} />
                </Popconfirm>
              )
            }
            const cid = row.flink_application_cluster_id
            const isApp = (row.flink_sql_submit_mode || '').toString().toLowerCase() === 'kubernetes_application'
            if (isApp && cid) {
              return (
                <Tooltip title="尚无 Job ID 时无法在平台侧调用 JM 停止接口；请配置 FLINK_K8S_APPLICATION_JM_REST_TEMPLATE 或在 Flink/K8s 控制台停止该 Application 集群。">
                  <Text type="secondary">—</Text>
                </Tooltip>
              )
            }
            return <Text type="secondary">—</Text>
          },
        }]
      : []),
    ...(canRemove
      ? [{
          title: '删除',
          key: 'del',
          width: 76,
          render: (_: unknown, row: any) => (
            <Popconfirm
              title="删除此作业记录？"
              description="将尝试停止 Flink 并删除本平台记录。"
              onConfirm={() => handleRemove(row)}
              okText="删除"
              okButtonProps={{ danger: true }}
            >
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
            </Popconfirm>
          ),
        }]
      : []),
  ]

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>作业运维</Typography.Title>
      <Paragraph type="secondary" style={{ marginBottom: 16, maxWidth: 960 }}>
        对标成熟实时平台：<strong>部署模型</strong>（Session / K8s Application）、<strong>集群与 Job 标识</strong>、<strong>最近提交人时</strong>与<strong>就绪度提示</strong>集中展示；诊断抽屉内合并「平台同步结果」与 JM 异常，便于与 Flink Web UI / K8s 控制台交叉验证。
        逻辑编辑请前往 <Link to={R.stream.studio}>作业开发</Link>。
      </Paragraph>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="控制台链接：有 Flink Job ID 时指向作业详情；K8s Application 若已配置 JM REST（FLINK_K8S_APPLICATION_JM_REST_TEMPLATE），在尚未回填 jobId 时也可打开 JM 总览页。Session 模式仍依赖 FLINK_UI_URL（或默认同 FLINK_URL）。"
      />

      <Space style={{ marginBottom: 16 }} wrap>
        <Button icon={<ReloadOutlined />} onClick={() => loadJobs(true)} loading={loading}>刷新列表</Button>
        <Button type="primary" onClick={syncAll}>全量同步状态</Button>
      </Space>
      <Table rowKey="id" loading={loading} dataSource={jobs} columns={columns as any} scroll={{ x: 1520 }} pagination={{ pageSize: 12 }} />

      <Drawer
        title={diagRow ? `诊断 · ${diagRow.name}` : '诊断'}
        width={720}
        open={diagOpen}
        onClose={() => { setDiagOpen(false); setDiagRow(null); setDiagExceptions(null); setDiagSync(null) }}
        destroyOnClose
      >
        <Typography.Title level={5} style={{ marginTop: 0 }}>平台侧同步（最近一次拉取）</Typography.Title>
        {diagSync ? (
          <pre style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            background: '#f0f5ff',
            border: '1px solid #d6e4ff',
            borderRadius: 8,
            padding: 12,
            fontSize: 12,
            maxHeight: 220,
            overflow: 'auto',
          }}>
            {JSON.stringify(diagSync, null, 2)}
          </pre>
        ) : (
          <Text type="secondary">加载中…</Text>
        )}

        {diagRow?.flink_operational?.hints?.length ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>就绪度与运维建议</Typography.Title>
            <Alert
              type={diagRow.flink_operational.readiness === 'blocked' ? 'error' : diagRow.flink_operational.readiness === 'warning' ? 'warning' : 'info'}
              showIcon
              message={`就绪度：${diagRow.flink_operational.readiness}`}
              description={(
                <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13 }}>
                  {diagRow.flink_operational.hints.map((h: string, i: number) => (
                    <li key={i}>{h}</li>
                  ))}
                </ul>
              )}
            />
          </>
        ) : null}

        {diagRow?.last_submit_error ? (
          <>
            <Typography.Title level={5} style={{ marginTop: 16 }}>最近一次提交到 Flink 失败（启动阶段）</Typography.Title>
            <pre style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              background: '#fff2f0',
              border: '1px solid #ffccc7',
              borderRadius: 8,
              padding: 12,
              fontSize: 12,
              maxHeight: 280,
              overflow: 'auto',
            }}>
              {diagRow.last_submit_error}
            </pre>
          </>
        ) : (
          <Paragraph type="secondary" style={{ marginTop: 16 }}>暂无最近一次提交失败的记录。</Paragraph>
        )}

        {diagRow?.flink_console_url && (
          <p style={{ marginTop: 12 }}>
            <a href={diagRow.flink_console_url} target="_blank" rel="noreferrer">
              <LinkOutlined /> Flink Web UI（作业详情或 JM 总览）
            </a>
          </p>
        )}

        <Typography.Title level={5} style={{ marginTop: 16 }}>Flink JobManager · 运行时异常（REST）</Typography.Title>
        {!diagRow?.flink_job_id ? (
          <Text type="secondary">
            尚无 Flink Job ID，无法拉取 /jobs/&lt;id&gt;/exceptions。
            {diagRow?.flink_application_cluster_id ? ' Application 模式下请先在 Web UI 确认 Job 是否已出现，或检查是否已配置 JM REST 模板以自动回填 Job ID。' : ''}
          </Text>
        ) : (
          <pre style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            background: '#f5f5f5',
            borderRadius: 8,
            padding: 12,
            fontSize: 12,
            maxHeight: 360,
            overflow: 'auto',
          }}>
            {diagExceptions ? JSON.stringify(diagExceptions, null, 2) : '加载中…'}
          </pre>
        )}
      </Drawer>
    </div>
  )
}

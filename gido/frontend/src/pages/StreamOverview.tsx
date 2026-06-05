/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { Card, Row, Col, Statistic, Alert, Spin, Descriptions, Button, Space, Select } from 'antd'
import { CloudServerOutlined, DatabaseOutlined, ThunderboltOutlined, ApiOutlined, ReloadOutlined } from '@ant-design/icons'
import { streamingApi } from '../api'
import { useAppStore } from '../store'

type FlinkProfileRow = { id: number; name: string }

export default function StreamOverviewPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id

  const [data, setData] = useState<Record<string, any> | null>(null)
  const [conn, setConn] = useState<any | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [profiles, setProfiles] = useState<FlinkProfileRow[]>([])
  const [profilesLoading, setProfilesLoading] = useState(false)
  /** null = 平台默认；数字 = 当前工作空间下的命名 Flink 集群连接 */
  const [probeProfileId, setProbeProfileId] = useState<number | null>(null)

  const queryParams = useMemo(() => {
    if (probeProfileId != null && wsId != null) {
      return { workspace_id: wsId, flink_session_profile_id: probeProfileId }
    }
    return undefined
  }, [probeProfileId, wsId])

  useEffect(() => {
    setProbeProfileId(null)
  }, [wsId])

  useEffect(() => {
    if (!wsId) {
      setProfiles([])
      return
    }
    let cancelled = false
    setProfilesLoading(true)
    streamingApi
      .listFlinkSessionProfiles(wsId)
      .then((rows: unknown) => {
        if (!cancelled) {
          const list = Array.isArray(rows) ? rows : []
          setProfiles(
            list.map((r: { id: number; name?: string }) => ({
              id: r.id,
              name: (r.name || '').trim() || `连接 #${r.id}`,
            })),
          )
        }
      })
      .catch(() => {
        if (!cancelled) setProfiles([])
      })
      .finally(() => {
        if (!cancelled) setProfilesLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [wsId])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      let c: any
      try {
        c = await streamingApi.connectivity(queryParams)
      } catch (e: any) {
        c = {
          _failed: true,
          detail: e?.response?.data?.detail || e.message || String(e),
        }
      }
      setConn(c)
      try {
        const ov = await streamingApi.overview(queryParams)
        setData(ov)
        setErr(null)
      } catch (e: any) {
        setData(null)
        setErr(e?.response?.data?.detail || e.message || '无法连接 JobManager')
      }
    } finally {
      setLoading(false)
    }
  }, [queryParams])

  useEffect(() => {
    load()
  }, [load])

  if (loading && !conn) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    )
  }

  const slotsTotal = data?.['slots-total'] ?? data?.slotsTotal
  const slotsAvail = data?.['slots-available'] ?? data?.slotsAvailable
  const tm = data?.taskmanagers ?? data?.['taskmanagers']

  const jmOk = conn?.jobmanager?.ok
  const gwOk = conn?.sql_gateway?.ok
  const hints: string[] = conn?.hints || []
  const probe = conn?.probe

  const probeLabel =
    probe?.kind === 'flink_session_profile'
      ? `命名连接「${probe.profile_name || ''}」(#${probe.profile_id})`
      : '平台默认（环境 + 系统 Flink 集成）'

  const mergeConfigHint =
    probe?.kind === 'flink_session_profile'
      ? '以下为当前命名连接与平台默认合并后的基址（连接中非空字段覆盖同名集成项）。'
      : '合并 .env / 环境变量 + 系统管理 → 集成与连接 → Flink（库内有覆盖时以库内为准）。'

  return (
    <div>
      <Space wrap style={{ marginBottom: 16 }} align="center">
        <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>
          刷新
        </Button>
        <span style={{ color: '#666' }}>探测目标</span>
        <Select
          style={{ minWidth: 280 }}
          loading={profilesLoading}
          disabled={!wsId}
          value={probeProfileId === null ? '__platform__' : probeProfileId}
          onChange={(v) => {
            if (v === '__platform__') setProbeProfileId(null)
            else setProbeProfileId(Number(v))
          }}
          options={[
            { value: '__platform__', label: '平台默认（全局 Flink 配置）' },
            ...profiles.map((p) => ({ value: p.id, label: `${p.name} (#${p.id})` })),
          ]}
        />
        {!wsId && (
          <span style={{ color: '#999', fontSize: 12 }}>请先在顶部选择工作空间，即可按「Flink 集群连接」逐项探测。</span>
        )}
      </Space>

      {conn?._failed && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="连通性接口失败" description={conn.detail} />
      )}

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={`当前探测：${probeLabel}`}
        description={
          wsId
            ? '与实时开发里作业可选的「Flink 集群连接」使用同一套合并规则；在此切换即可核对各套地址是否对后端可达。'
            : '未选工作空间时仅展示平台默认；选择空间后可下拉切换该空间下的命名连接。'
        }
      />

      {hints.length > 0 && (
        <Alert
          type={jmOk && gwOk ? 'info' : 'warning'}
          showIcon
          style={{ marginBottom: 16 }}
          message="连接与配置提示"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {hints.map((h, i) => (
                <li key={i}>{h}</li>
              ))}
            </ul>
          }
        />
      )}

      <Card title={<><ApiOutlined /> GIDO → Flink 连通性</>} size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="JobManager（配置 → 实际 HTTP）">
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              <strong>合并配置基址</strong>（{mergeConfigHint}）{' '}
              <code style={{ wordBreak: 'break-all' }}>{conn?.jobmanager?.configured_url || '—'}</code>
            </div>
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              <strong>本进程实际请求</strong>（容器内可能把 127.0.0.1 改写为 host.docker.internal）：{' '}
              <code style={{ wordBreak: 'break-all' }}>{conn?.jobmanager?.effective_url ?? conn?.jobmanager?.url ?? '—'}</code>
            </div>
            {conn?.jobmanager?.running_in_docker && (
              <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>后端进程判定为：运行在 Docker 容器内</div>
            )}
            {conn?.jobmanager?.localhost_rewritten_for_docker && (
              <div style={{ fontSize: 12, color: '#d48806', marginBottom: 4 }}>
                已对 localhost/127.0.0.1 做容器内改写；Linux 若仍失败，请在 compose 为 JobManager 可达性配置 extra_hosts（见上方提示）。
              </div>
            )}
            {jmOk ? <span style={{ color: '#52c41a' }}>状态：可达</span> : <span style={{ color: '#faad14' }}>状态：不可达</span>}
            {conn?.jobmanager?.error && (
              <div style={{ color: '#cf1322', fontSize: 12, marginTop: 8 }}>{conn.jobmanager.error}</div>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="SQL Gateway">
            {conn?.sql_gateway?.configured
              ? conn?.sql_gateway?.base_url || '已配置'
              : '未配置 FLINK_SQL_GATEWAY_URL'}
            {conn?.sql_gateway?.configured &&
              (gwOk ? (
                <span style={{ color: '#52c41a', marginLeft: 8 }}>
                  /v1/info 正常
                </span>
              ) : (
                <span style={{ color: '#faad14', marginLeft: 8 }}>
                  不可达或异常
                </span>
              ))}
            {conn?.sql_gateway?.error && (
              <div style={{ color: '#cf1322', fontSize: 12, marginTop: 4 }}>{conn.sql_gateway.error}</div>
            )}
            {conn?.sql_gateway?.info && (
              <pre style={{ fontSize: 11, marginTop: 8, background: '#f5f5f5', padding: 8, borderRadius: 4 }}>
                {JSON.stringify(conn.sql_gateway.info, null, 2)}
              </pre>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="Gateway 提交作业时的 JM REST 目标">
            {conn?.gateway_jm_execution_target && Object.keys(conn.gateway_jm_execution_target).length > 0
              ? JSON.stringify(conn.gateway_jm_execution_target)
              : '—'}
          </Descriptions.Item>
          <Descriptions.Item label="Flink Web UI 基址（作业链接用）">{conn?.flink_ui_base ?? '—'}</Descriptions.Item>
        </Descriptions>
      </Card>

      {err && !data && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }} message="JobManager /overview 不可用" description={err} />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="TaskManagers" value={data ? (tm ?? '—') : '—'} prefix={<CloudServerOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="总 Slot" value={data ? (slotsTotal ?? '—') : '—'} prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="可用 Slot" value={data ? (slotsAvail ?? '—') : '—'} prefix={<ThunderboltOutlined />} />
          </Card>
        </Col>
      </Row>
      <Card title="Flink /overview 原始数据" style={{ marginTop: 16 }}>
        {!data ? (
          <div style={{ color: '#999' }}>无数据（JobManager 未连通）</div>
        ) : (
          <Descriptions column={1} size="small">
            {Object.entries(data).map(([k, v]) => (
              <Descriptions.Item key={k} label={k}>
                {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </Descriptions.Item>
            ))}
          </Descriptions>
        )}
      </Card>
    </div>
  )
}

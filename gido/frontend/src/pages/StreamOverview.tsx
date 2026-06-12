/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { useState, useEffect, useCallback } from 'react'
import {
  Alert, Badge, Button, Card, Col, Descriptions, Row, Space, Spin, Statistic, Table, Tag, Tooltip, Typography,
} from 'antd'
import {
  CloudServerOutlined, CheckCircleOutlined, CloseCircleOutlined, ClusterOutlined,
  ContainerOutlined, ReloadOutlined, SyncOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { Link } from 'react-router-dom'
import { streamingApi } from '../api'
import { useAppStore } from '../store'
import { R } from '../routes'

const { Text, Paragraph } = Typography

type DeploymentRow = {
  name?: string
  namespace?: string
  workspace_id?: string
  job_id?: string
  job_type?: string
  lifecycle?: string
  health?: string
  flink_job_id?: string
  error?: string
  spec_state?: string
  image?: string
  flink_version?: string
  job_manager_status?: Record<string, unknown>
  task_manager_status?: Record<string, unknown>
  created_at?: string
}

const HEALTH_COLOR: Record<string, string> = {
  healthy: 'success',
  failed: 'error',
  suspended: 'warning',
  starting: 'processing',
  unknown: 'default',
}

const HEALTH_LABEL: Record<string, string> = {
  healthy: '运行中',
  failed: '失败',
  suspended: '已暂停',
  starting: '启动中',
  unknown: '未知',
}

function PodHealthCard({ title, status }: { title: string; status?: Record<string, unknown> | string | null }) {
  if (!status) {
    return (
      <Card size="small" style={{ textAlign: 'center', minHeight: 88 }}>
        <Text type="secondary">{title}</Text>
        <div style={{ marginTop: 8 }}><Badge status="default" text="无数据" /></div>
      </Card>
    )
  }
  const raw = typeof status === 'string' ? status : (status.status as string) || JSON.stringify(status)
  const ok = /ready|running|stable/i.test(String(raw))
  return (
    <Card size="small" style={{ textAlign: 'center', minHeight: 88 }}>
      <Text type="secondary">{title}</Text>
      <div style={{ marginTop: 8, fontSize: 22 }}>
        {ok ? <CheckCircleOutlined style={{ color: '#52c41a' }} /> : <SyncOutlined spin style={{ color: '#1677ff' }} />}
      </div>
      <Text style={{ fontSize: 12 }}>{String(raw)}</Text>
    </Card>
  )
}

export default function StreamOverviewPage() {
  const { currentWorkspace } = useAppStore()
  const wsId = currentWorkspace?.id

  const [data, setData] = useState<Record<string, any> | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!wsId) {
      setData(null)
      setErr(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const ov = await streamingApi.operatorOverview(wsId)
      setData(ov)
      setErr(null)
    } catch (e: any) {
      setData(null)
      setErr(e?.response?.data?.detail || e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [wsId])

  useEffect(() => { load() }, [load])

  if (!wsId) {
    return <Alert type="info" message="请先选择工作空间" showIcon />
  }

  if (loading && !data) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin size="large" />
      </div>
    )
  }

  const runtime = data?.runtime || {}
  const summary = data?.summary || {}
  const deployments: DeploymentRow[] = data?.deployments || []

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Flink 运行概览</h2>
          <Text type="secondary">Flink Kubernetes Operator 模式 — 查看命名空间内 FlinkDeployment 与运行时配置</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} loading={loading}>刷新</Button>
          <Link to={R.stream.monitor}><Button icon={<ThunderboltOutlined />}>作业运维</Button></Link>
        </Space>
      </div>

      {err && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message="加载概览失败" description={err} />
      )}

      {data?.operator_ready === false && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="Operator 提交未就绪"
          description={data.operator_ready_reason || '请检查 K8s 访问与 FLINK_OPERATOR_* 环境变量'}
        />
      )}

      {data?.k8s_error && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="Kubernetes 集群信息不可用"
          description={data.k8s_error}
        />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="FlinkDeployment"
              value={summary.deployments_total ?? 0}
              prefix={<ClusterOutlined />}
              suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {summary.running ?? 0} 运行</Text>}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="失败 / 暂停"
              value={summary.failed ?? 0}
              valueStyle={{ color: (summary.failed ?? 0) > 0 ? '#ff4d4f' : undefined }}
              prefix={<CloseCircleOutlined />}
              suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {summary.suspended ?? 0} 暂停</Text>}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="GIDO 作业"
              value={summary.jobs_total ?? 0}
              prefix={<ContainerOutlined />}
              suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {summary.jobs_running ?? 0} 运行</Text>}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="启动中"
              value={summary.starting ?? 0}
              prefix={<SyncOutlined spin={(summary.starting ?? 0) > 0} />}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} lg={14}>
          <Card title={<><CloudServerOutlined /> 运行时配置</>} size="small">
            <Descriptions column={{ xs: 1, sm: 2 }} size="small">
              <Descriptions.Item label="提交模式">
                <Tag color="purple">Flink Operator</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="K8s 命名空间">
                <Text code>{data?.namespace || runtime.operator_namespace || '—'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="运行时镜像" span={2}>
                <Paragraph copyable={{ text: runtime.runtime_image }} style={{ marginBottom: 0 }}>
                  <Text code style={{ wordBreak: 'break-all' }}>{runtime.runtime_image || '—'}</Text>
                </Paragraph>
              </Descriptions.Item>
              <Descriptions.Item label="Flink 版本">{runtime.flink_version || '—'}</Descriptions.Item>
              <Descriptions.Item label="Operator flinkVersion">
                <Text code>{runtime.flink_operator_version || '—'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Checkpoint 目录" span={2}>
                <Text code style={{ wordBreak: 'break-all' }}>{runtime.checkpoint_dir_default || '—'}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Paimon Warehouse" span={2}>
                <Text code style={{ wordBreak: 'break-all' }}>{runtime.paimon_warehouse_default || '—'}</Text>
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="Operator 架构" size="small">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 24, padding: '12px 0' }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, color: '#722ed1' }}><ClusterOutlined /></div>
                <Text strong>GIDO Backend</Text>
                <div><Text type="secondary" style={{ fontSize: 11 }}>创建 FlinkDeployment CR</Text></div>
              </div>
              <Text type="secondary">→</Text>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, color: '#1677ff' }}><CloudServerOutlined /></div>
                <Text strong>Flink Operator</Text>
                <div><Text type="secondary" style={{ fontSize: 11 }}>协调 JM / TM Pod</Text></div>
              </div>
              <Text type="secondary">→</Text>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, color: '#52c41a' }}><ThunderboltOutlined /></div>
                <Text strong>Flink Job</Text>
                <div><Text type="secondary" style={{ fontSize: 11 }}>SQL / JAR Application</Text></div>
              </div>
            </div>
          </Card>
        </Col>
      </Row>

      <Card title="FlinkDeployment 列表" style={{ marginBottom: 16 }}>
        <Table
          size="small"
          rowKey="name"
          loading={loading}
          dataSource={deployments}
          pagination={{ pageSize: 10, showSizeChanger: true }}
          locale={{ emptyText: '当前工作空间暂无 FlinkDeployment（提交作业后将自动创建）' }}
          columns={[
            {
              title: '部署名',
              dataIndex: 'name',
              ellipsis: true,
              render: (name: string) => (
                <Tooltip title="FlinkDeployment CR 名称">
                  <Text code style={{ fontSize: 11 }}>{name}</Text>
                </Tooltip>
              ),
            },
            {
              title: 'GIDO 作业',
              key: 'job',
              width: 100,
              render: (_: unknown, row: DeploymentRow) => (
                row.job_id
                  ? <Link to={R.stream.monitor}>#{row.job_id}</Link>
                  : <Text type="secondary">—</Text>
              ),
            },
            {
              title: '类型',
              dataIndex: 'job_type',
              width: 64,
              render: (t: string) => t ? <Tag>{t.toUpperCase()}</Tag> : '—',
            },
            {
              title: '健康',
              dataIndex: 'health',
              width: 88,
              render: (h: string) => (
                <Tag color={HEALTH_COLOR[h] || 'default'}>{HEALTH_LABEL[h] || h || '—'}</Tag>
              ),
            },
            {
              title: 'Lifecycle',
              dataIndex: 'lifecycle',
              width: 120,
              render: (lc: string) => lc ? <Tag>{lc}</Tag> : '—',
            },
            {
              title: 'Flink JobId',
              dataIndex: 'flink_job_id',
              width: 120,
              ellipsis: true,
              render: (id: string) => id ? <Text code style={{ fontSize: 11 }}>{id.slice(0, 8)}…</Text> : '—',
            },
            {
              title: 'JM / TM',
              key: 'pods',
              width: 140,
              render: (_: unknown, row: DeploymentRow) => {
                const jm = row.job_manager_status
                const tm = row.task_manager_status
                const jmOk = jm && /ready|running|stable/i.test(JSON.stringify(jm))
                const tmOk = tm && /ready|running|stable/i.test(JSON.stringify(tm))
                return (
                  <Space size={4}>
                    <Tooltip title={`JobManager: ${jm ? JSON.stringify(jm) : '无'}`}>
                      <Tag color={jmOk ? 'success' : 'default'}>JM</Tag>
                    </Tooltip>
                    <Tooltip title={`TaskManager: ${tm ? JSON.stringify(tm) : '无'}`}>
                      <Tag color={tmOk ? 'success' : 'default'}>TM</Tag>
                    </Tooltip>
                  </Space>
                )
              },
            },
            {
              title: '错误',
              dataIndex: 'error',
              ellipsis: true,
              render: (e: string) => e ? <Text type="danger" style={{ fontSize: 12 }}>{e}</Text> : '—',
            },
          ]}
          expandable={{
            expandedRowRender: (row: DeploymentRow) => (
              <Row gutter={12}>
                <Col xs={24} sm={12}>
                  <PodHealthCard title="JobManager" status={row.job_manager_status as Record<string, unknown>} />
                </Col>
                <Col xs={24} sm={12}>
                  <PodHealthCard title="TaskManager" status={row.task_manager_status as Record<string, unknown>} />
                </Col>
                {row.image && (
                  <Col span={24} style={{ marginTop: 8 }}>
                    <Text type="secondary">镜像：</Text>
                    <Text code style={{ fontSize: 11 }}>{row.image}</Text>
                  </Col>
                )}
              </Row>
            ),
            rowExpandable: (row) => Boolean(row.job_manager_status || row.task_manager_status || row.image),
          }}
        />
      </Card>
    </div>
  )
}

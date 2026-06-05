/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Alert, Card, Col, Row, Statistic, Table } from 'antd'
import { useServiceData, useWorkspaceId } from './ServiceContext'

export default function ServiceMonitorPage() {
  const wsId = useWorkspaceId()
  const { stats, logs, loading } = useServiceData()

  if (!wsId) return <Alert type="info" message="请先选择工作空间" showIcon />

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>调用监控</h2>
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}><Card loading={loading}><Statistic title="近 7 日调用" value={stats?.total_calls ?? 0} /></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card loading={loading}><Statistic title="错误次数" value={stats?.error_calls ?? 0} /></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card loading={loading}><Statistic title="错误率" value={((stats?.error_rate ?? 0) * 100).toFixed(2)} suffix="%" /></Card></Col>
        <Col xs={24} sm={12} lg={6}><Card loading={loading}><Statistic title="平均延迟(ms)" value={stats?.avg_latency_ms ?? 0} precision={1} /></Card></Col>
      </Row>

      <Table
        title={() => '最近调用日志'}
        dataSource={logs}
        rowKey="id"
        loading={loading}
        size="small"
        scroll={{ x: 900 }}
        columns={[
          { title: 'TraceId', dataIndex: 'trace_id', width: 140, ellipsis: true },
          { title: 'API', dataIndex: 'api_id', width: 70 },
          { title: 'App', dataIndex: 'app_id', width: 70, render: (v: number) => v || '—' },
          { title: '状态', dataIndex: 'status_code', width: 70 },
          { title: '行数', dataIndex: 'row_count', width: 60 },
          { title: '延迟(ms)', dataIndex: 'latency_ms', width: 90, render: (v: number) => v?.toFixed?.(1) ?? v },
          { title: '缓存', dataIndex: 'cache_hit', width: 60, render: (v: boolean) => v ? '命中' : '—' },
          { title: '时间', dataIndex: 'created_at', width: 170 },
          { title: '错误', dataIndex: 'error_message', ellipsis: true },
        ]}
      />
    </div>
  )
}

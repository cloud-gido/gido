/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import { useNavigate } from 'react-router-dom'
import { ApiOutlined, AppstoreOutlined, LineChartOutlined, PlusOutlined } from '@ant-design/icons'
import { useServiceData, useWorkspaceId } from './ServiceContext'
import { R } from '../../routes'
import { STATUS_COLOR } from './shared'
import { can, P } from '../../perm'
import { useAppStore } from '../../store'

const { Text } = Typography

export default function ServiceOverviewPage() {
  const navigate = useNavigate()
  const wsId = useWorkspaceId()
  const { user, currentWorkspace } = useAppStore()
  const { apis, apps, stats, logs, loading } = useServiceData()
  const canWrite = can(user, P.GIDO_SERVICE_WRITE, currentWorkspace)

  if (!wsId) {
    return <Alert type="info" message="请先选择工作空间" showIcon />
  }

  const onlineApis = apis.filter(a => a.status === 'online').length

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>服务概览</h2>
        <Text type="secondary">将 SQL 查询封装为 HTTP API，通过 AppKey / AppSecret 对外提供数据能力</Text>
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}><Statistic title="API 总数" value={apis.length} suffix={<Text type="secondary" style={{ fontSize: 13 }}>/ {onlineApis} 已上线</Text>} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}><Statistic title="消费者应用" value={apps.length} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}><Statistic title="近 7 日调用" value={stats?.total_calls ?? 0} /></Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card loading={loading}><Statistic title="平均延迟 (ms)" value={stats?.avg_latency_ms ?? 0} precision={1} /></Card>
        </Col>
      </Row>

      <Space wrap style={{ marginBottom: 16 }}>
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate(R.service.apis)}>
            新建 API
          </Button>
        )}
        <Button icon={<ApiOutlined />} onClick={() => navigate(R.service.apis)}>API 开发</Button>
        <Button icon={<AppstoreOutlined />} onClick={() => navigate(R.service.apps)}>应用管理</Button>
        <Button icon={<LineChartOutlined />} onClick={() => navigate(R.service.monitor)}>调用监控</Button>
      </Space>

      <Card title="最近上线 API" loading={loading}>
        <Table
          size="small"
          rowKey="id"
          dataSource={apis.slice(0, 8)}
          pagination={false}
          columns={[
            { title: '名称', dataIndex: 'name', ellipsis: true },
            { title: 'API Code', dataIndex: 'api_code', width: 140 },
            {
              title: '状态', dataIndex: 'status', width: 90,
              render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{s === 'online' ? '已上线' : s === 'offline' ? '已下线' : '草稿'}</Tag>,
            },
            { title: '开放路径', dataIndex: 'open_path', ellipsis: true, render: (p: string) => <Text code style={{ fontSize: 11 }}>{p}</Text> },
          ]}
        />
      </Card>

      <Card title="最近调用" style={{ marginTop: 16 }} loading={loading}>
        <Table
          size="small"
          rowKey="id"
          dataSource={logs.slice(0, 10)}
          pagination={false}
          columns={[
            { title: 'TraceId', dataIndex: 'trace_id', width: 140, ellipsis: true },
            { title: 'API', dataIndex: 'api_id', width: 70 },
            { title: '状态', dataIndex: 'status_code', width: 70 },
            { title: '延迟(ms)', dataIndex: 'latency_ms', width: 90, render: (v: number) => v?.toFixed?.(1) ?? v },
            { title: '时间', dataIndex: 'created_at', width: 170 },
            { title: '错误', dataIndex: 'error_message', ellipsis: true },
          ]}
        />
      </Card>
    </div>
  )
}

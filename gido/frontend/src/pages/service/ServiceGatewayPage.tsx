/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
import { Alert, Card, Collapse, Typography } from 'antd'
import { useServiceData, useWorkspaceId } from './ServiceContext'

const { Text, Paragraph, Title } = Typography

export default function ServiceGatewayPage() {
  const wsId = useWorkspaceId()
  const { apis } = useServiceData()
  const base = typeof window !== 'undefined' ? `${window.location.origin}/api/open/v1/ws/${wsId}` : `/api/open/v1/ws/${wsId}`

  if (!wsId) return <Alert type="info" message="请先选择工作空间" showIcon />

  const onlineApis = apis.filter(a => a.status === 'online')

  return (
    <div>
      <Title level={4} style={{ marginTop: 0 }}>开放网关</Title>
      <Paragraph type="secondary">
        对外 HTTP 入口统一为 <Text code>/api/open/v1/ws/{'{workspace_id}'}/{'{api_code}'}</Text>，
        通过消费者应用的 AppKey / AppSecret 鉴权后调用已上线 API。
      </Paragraph>

      <Card title="鉴权" style={{ marginBottom: 16 }}>
        <Paragraph>
          请求头必填：<Text code>X-App-Key</Text>、<Text code>X-App-Secret</Text>
        </Paragraph>
        <Paragraph type="secondary">应用须在「应用管理」中创建，并在「授权 API」里勾选目标 API。</Paragraph>
      </Card>

      <Card title="调用示例" style={{ marginBottom: 16 }}>
        <pre style={{ background: '#f8fafc', padding: 12, borderRadius: 8, fontSize: 12, overflow: 'auto' }}>{`curl -G "${base}/get_all_order" \\
  -H "X-App-Key: YOUR_APP_KEY" \\
  -H "X-App-Secret: YOUR_APP_SECRET" \\
  --data-urlencode "fixture_id=FX001" \\
  --data-urlencode "page_no=1" \\
  --data-urlencode "page_size=20"`}</pre>
        <Paragraph type="secondary">GET 参数放 query；POST 可放 JSON body。分页参数：<Text code>page_no</Text>、<Text code>page_size</Text>。</Paragraph>
      </Card>

      <Card title="响应格式">
        <pre style={{ background: '#f8fafc', padding: 12, borderRadius: 8, fontSize: 12 }}>{`{
  "trace_id": "...",
  "data": {
    "columns": ["col1", "col2"],
    "rows": [[...], [...]],
    "page_no": 1,
    "page_size": 20,
    "cache_hit": false
  }
}`}</pre>
      </Card>

      <Card title={`已上线 API（${onlineApis.length}）`} style={{ marginTop: 16 }}>
        {onlineApis.length === 0 ? (
          <Alert type="info" message="暂无已上线 API" showIcon />
        ) : (
          <Collapse
            items={onlineApis.map(api => ({
              key: String(api.id),
              label: `${api.name} (${api.api_code})`,
              children: (
                <div>
                  <div>路径：<Text code copyable>{`${base}/${api.api_code}`}</Text></div>
                  <div style={{ marginTop: 8 }}>方法：{api.http_method || 'GET'}</div>
                  <div style={{ marginTop: 4 }}>参数：{(api.params || []).map((p: any) => p.name).join(', ') || '—'}</div>
                </div>
              ),
            }))}
          />
        )}
      </Card>
    </div>
  )
}

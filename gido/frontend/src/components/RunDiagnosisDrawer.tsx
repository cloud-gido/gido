/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */
import { useEffect, useState } from 'react'
import { Drawer, Alert, Spin, Tag, Empty, Descriptions, Space, Button } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { operationApi } from '../api'

/**
 * 运行诊断抽屉：实例中心与告警中心共用。
 * 回答「这次为什么没跑 / 还没跑完」，结论按严重度排序，阻塞项排在最前。
 */
export type DiagnosisTarget = {
  workspaceId: number
  workflowId: number
  workflowName?: string
  businessDate?: string | null
}

type Finding = {
  code: string
  level: 'blocker' | 'warning' | 'info' | 'ok'
  title: string
  detail: string
  action?: string | null
}

const LEVEL_META: Record<Finding['level'], { color: string; label: string; alert: 'error' | 'warning' | 'info' | 'success' }> = {
  blocker: { color: 'red', label: '阻塞', alert: 'error' },
  warning: { color: 'orange', label: '注意', alert: 'warning' },
  info: { color: 'blue', label: '参考', alert: 'info' },
  ok: { color: 'green', label: '正常', alert: 'success' },
}

export default function RunDiagnosisDrawer({
  target,
  onClose,
}: {
  target: DiagnosisTarget | null
  onClose: () => void
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')
  const [result, setResult] = useState<any>(null)

  const run = async () => {
    if (!target) return
    setLoading(true)
    setError('')
    try {
      const res: any = await operationApi.diagnose(target.workspaceId, target.workflowId, target.businessDate || undefined)
      setResult(res)
    } catch (e: any) {
      setError(e?.response?.data?.detail || '诊断失败')
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!target) {
      setResult(null)
      setError('')
      return
    }
    run()
    // 目标变了就重新诊断；抽屉关闭时清空
  }, [target?.workflowId, target?.businessDate, target?.workspaceId])

  const findings: Finding[] = Array.isArray(result?.findings) ? result.findings : []

  return (
    <Drawer
      title="运行诊断"
      open={!!target}
      onClose={onClose}
      width={620}
      extra={<Button icon={<ReloadOutlined />} onClick={run} loading={loading}>重新诊断</Button>}
    >
      {loading && !result ? (
        <Spin />
      ) : error ? (
        <Alert type="error" showIcon message={error} />
      ) : result ? (
        <>
          <Descriptions size="small" column={1} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="工作流">
              {result.workflow_name || target?.workflowName || `#${result.workflow_id}`}
            </Descriptions.Item>
            <Descriptions.Item label="业务日期">{result.business_date || '—'}</Descriptions.Item>
            <Descriptions.Item label="本日期运行">
              {Array.isArray(result.instance_ids) && result.instance_ids.length
                ? result.instance_ids.map((id: number) => `#${id}`).join('、')
                : '无'}
            </Descriptions.Item>
            <Descriptions.Item label="结论">
              <strong>{result.verdict || '—'}</strong>
            </Descriptions.Item>
          </Descriptions>
          {findings.length ? (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {findings.map((f) => {
                const meta = LEVEL_META[f.level] || LEVEL_META.info
                return (
                  <Alert
                    key={f.code}
                    type={meta.alert}
                    showIcon
                    message={
                      <Space size={6}>
                        <Tag color={meta.color}>{meta.label}</Tag>
                        <span>{f.title}</span>
                      </Space>
                    }
                    description={
                      <div>
                        <div>{f.detail}</div>
                        {f.action ? (
                          <div style={{ marginTop: 6, color: '#666' }}>建议：{f.action}</div>
                        ) : null}
                      </div>
                    }
                  />
                )
              })}
            </Space>
          ) : (
            <Empty description="没有得出结论" />
          )}
        </>
      ) : (
        <Empty description="暂无诊断结果" />
      )}
    </Drawer>
  )
}

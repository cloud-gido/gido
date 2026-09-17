/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Alert, Checkbox, Empty, Space, Tag, Typography } from 'antd'

const { Text } = Typography

export type StreamRiskLevel = 'low' | 'medium' | 'high' | 'critical' | 'blocker'

export interface StreamRiskItem {
  code: string
  confirmation_code?: string
  level: StreamRiskLevel | string
  message: string
  statement_index?: number | null
  statement_type?: string | null
  object_name?: string | null
  is_paimon?: boolean
  requires_confirmation?: boolean
}

export interface StreamRiskAssessment {
  schema_version: string | number
  level: StreamRiskLevel | string
  assessment_hash: string
  sql_hash: string
  requires_confirmation: boolean
  risks: StreamRiskItem[]
}

export function riskNeedsStrongConfirmation(assessment?: StreamRiskAssessment | null): boolean {
  const level = String(assessment?.level || '').toLowerCase()
  return level === 'high' || level === 'critical'
}

export function requiredRiskCodes(assessment?: StreamRiskAssessment | null): string[] {
  return [...new Set((assessment?.risks || [])
    .filter(risk => (
      risk.requires_confirmation
      || ['high', 'critical'].includes(String(risk.level).toLowerCase())
    ))
    .map(risk => risk.confirmation_code || risk.code))]
}

const levelMeta: Record<string, { color: string; alert: 'success' | 'info' | 'warning' | 'error' }> = {
  low: { color: 'green', alert: 'success' },
  medium: { color: 'blue', alert: 'info' },
  high: { color: 'orange', alert: 'warning' },
  critical: { color: 'red', alert: 'error' },
  blocker: { color: 'red', alert: 'error' },
}

export interface StreamRiskAssessmentPanelProps {
  assessment?: StreamRiskAssessment | null
  loading?: boolean
  error?: string | null
  confirmedRiskCodes?: string[]
  onConfirmedRiskCodesChange?: (codes: string[]) => void
  compact?: boolean
}

export default function StreamRiskAssessmentPanel({
  assessment,
  loading = false,
  error,
  confirmedRiskCodes = [],
  onConfirmedRiskCodesChange,
  compact = false,
}: StreamRiskAssessmentPanelProps) {
  if (loading) return <Alert showIcon type="info" message="正在分析 Flink SQL 风险…" />
  if (error) return <Alert showIcon type="warning" message="风险分析失败" description={error} />
  if (!assessment) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无风险评估" />

  const overall = levelMeta[String(assessment.level).toLowerCase()] || levelMeta.medium
  if (!assessment.risks?.length) {
    return <Alert showIcon type="success" message="未发现 SQL 发布风险" />
  }

  const toggle = (code: string, checked: boolean) => {
    const next = checked
      ? [...new Set([...confirmedRiskCodes, code])]
      : confirmedRiskCodes.filter(item => item !== code)
    onConfirmedRiskCodesChange?.(next)
  }

  return (
    <Space direction="vertical" size={compact ? 8 : 12} style={{ width: '100%' }}>
      <Alert
        showIcon
        type={overall.alert}
        message={(
          <Space wrap>
            <span>综合风险等级</span>
            <Tag color={overall.color}>{String(assessment.level).toUpperCase()}</Tag>
            <Text type="secondary">{assessment.risks.length} 项</Text>
          </Space>
        )}
      />
      {assessment.risks.map((risk, index) => {
        const meta = levelMeta[String(risk.level).toLowerCase()] || levelMeta.medium
        const needsConfirmation = Boolean(risk.requires_confirmation)
          || ['high', 'critical'].includes(String(risk.level).toLowerCase())
        const confirmationCode = risk.confirmation_code || risk.code
        const key = `${risk.code}-${risk.statement_index ?? index}-${risk.object_name || ''}`
        return (
          <Alert
            key={key}
            showIcon
            type={meta.alert}
            message={(
              <Space wrap size={6}>
                <Tag color={meta.color}>{String(risk.level).toUpperCase()}</Tag>
                <Text strong>{risk.code}</Text>
                {risk.is_paimon ? <Tag color="purple">Paimon</Tag> : null}
                {risk.statement_index != null ? <Tag>语句 #{Number(risk.statement_index) + 1}</Tag> : null}
                {risk.statement_type ? <Tag>{risk.statement_type}</Tag> : null}
                {risk.object_name ? <Text code>{risk.object_name}</Text> : null}
              </Space>
            )}
            description={risk.message}
            action={needsConfirmation && onConfirmedRiskCodesChange ? (
              <Checkbox
                checked={confirmedRiskCodes.includes(confirmationCode)}
                onChange={event => toggle(confirmationCode, event.target.checked)}
              >
                我已理解
              </Checkbox>
            ) : undefined}
          />
        )
      })}
    </Space>
  )
}

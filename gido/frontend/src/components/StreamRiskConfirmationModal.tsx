/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { Input, Modal, Space, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import StreamRiskAssessmentPanel, {
  requiredRiskCodes,
  type StreamRiskAssessment,
} from './StreamRiskAssessmentPanel'

const { Paragraph, Text } = Typography

interface StreamRiskConfirmationModalProps {
  open: boolean
  assessment?: StreamRiskAssessment | null
  jobName: string
  actionLabel: string
  loading?: boolean
  initialConfirmedRiskCodes?: string[]
  onCancel: () => void
  onConfirm: (confirmedRiskCodes: string[]) => void | Promise<void>
}

export default function StreamRiskConfirmationModal({
  open,
  assessment,
  jobName,
  actionLabel,
  loading = false,
  initialConfirmedRiskCodes = [],
  onCancel,
  onConfirm,
}: StreamRiskConfirmationModalProps) {
  const [confirmedRiskCodes, setConfirmedRiskCodes] = useState<string[]>([])
  const [targetInput, setTargetInput] = useState('')
  const requiredCodes = useMemo(() => requiredRiskCodes(assessment), [assessment])
  const criticalObjects = useMemo(() => [...new Set((assessment?.risks || [])
    .filter(risk => String(risk.level).toLowerCase() === 'critical' && risk.object_name)
    .map(risk => String(risk.object_name)))], [assessment])
  const isCritical = String(assessment?.level || '').toLowerCase() === 'critical'
  const expectedTarget = criticalObjects.length === 1 ? criticalObjects[0] : jobName
  const checksComplete = requiredCodes.every(code => confirmedRiskCodes.includes(code))
  const targetComplete = !isCritical || targetInput.trim() === expectedTarget

  useEffect(() => {
    if (!open) return
    setConfirmedRiskCodes(initialConfirmedRiskCodes)
    setTargetInput('')
  }, [open, assessment?.assessment_hash])

  return (
    <Modal
      title={`${actionLabel}风险确认`}
      open={open}
      width={760}
      onCancel={onCancel}
      onOk={() => onConfirm(confirmedRiskCodes)}
      okText={`确认${actionLabel}`}
      okButtonProps={{ danger: isCritical, disabled: !checksComplete || !targetComplete }}
      confirmLoading={loading}
      destroyOnHidden
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          高风险操作必须逐项确认。确认结果会与本次 SQL 评估哈希一起提交，SQL 变化后需重新确认。
        </Paragraph>
        <StreamRiskAssessmentPanel
          assessment={assessment}
          confirmedRiskCodes={confirmedRiskCodes}
          onConfirmedRiskCodesChange={setConfirmedRiskCodes}
        />
        {isCritical ? (
          <div>
            <Paragraph style={{ marginBottom: 8 }}>
              Critical 风险额外确认：请输入
              {' '}<Text code>{expectedTarget}</Text>
              {criticalObjects.length !== 1 ? '（存在多个目标对象，因此使用作业名）' : '（目标对象名）'}
            </Paragraph>
            <Input
              value={targetInput}
              onChange={event => setTargetInput(event.target.value)}
              placeholder={expectedTarget}
              status={targetInput && !targetComplete ? 'error' : undefined}
            />
          </div>
        ) : null}
      </Space>
    </Modal>
  )
}

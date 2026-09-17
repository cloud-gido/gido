/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import StreamRiskAssessmentPanel, {
  requiredRiskCodes,
  riskNeedsStrongConfirmation,
  type StreamRiskAssessment,
} from './StreamRiskAssessmentPanel'
import StreamRiskConfirmationModal from './StreamRiskConfirmationModal'

const assessment: StreamRiskAssessment = {
  schema_version: '1',
  level: 'critical',
  assessment_hash: 'assessment-1',
  sql_hash: 'sql-1',
  requires_confirmation: true,
  risks: [{
    code: 'DROP_PAIMON_TABLE',
    level: 'critical',
    message: '将删除目标表',
    statement_index: 1,
    statement_type: 'DROP TABLE',
    object_name: 'ods.orders',
    is_paimon: true,
    requires_confirmation: true,
  }],
}

afterEach(cleanup)

describe('StreamRiskAssessmentPanel', () => {
  it('renders statement and Paimon context and confirms by code', () => {
    const onChange = vi.fn()
    render(
      <StreamRiskAssessmentPanel
        assessment={assessment}
        confirmedRiskCodes={[]}
        onConfirmedRiskCodesChange={onChange}
      />,
    )

    expect(screen.getAllByText('CRITICAL')).toHaveLength(2)
    expect(screen.getByText('Paimon')).toBeInTheDocument()
    expect(screen.getByText('ods.orders')).toBeInTheDocument()
    expect(screen.getByText('语句 #2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(['DROP_PAIMON_TABLE'])
  })

  it('identifies strong confirmation and required codes', () => {
    expect(riskNeedsStrongConfirmation(assessment)).toBe(true)
    expect(requiredRiskCodes(assessment)).toEqual(['DROP_PAIMON_TABLE'])
  })

  it('requires the critical object name before confirmation', () => {
    const onConfirm = vi.fn()
    render(
      <StreamRiskConfirmationModal
        open
        assessment={assessment}
        jobName="orders-job"
        actionLabel="部署"
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )

    const confirm = screen.getByRole('button', { name: '确认部署' })
    expect(confirm).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByPlaceholderText('ods.orders'), {
      target: { value: 'ods.orders' },
    })
    expect(confirm).toBeEnabled()
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledWith(['DROP_PAIMON_TABLE'])
  })
})

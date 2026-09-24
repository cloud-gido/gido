/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * Cloud GIDO Fleet 许可状态条与升级提示。
 */
import { Alert, Button, Space, Tag, Typography } from 'antd'
import { useAppStore, type LicenseStatus } from '../../store'

const PLAN_LABEL: Record<string, string> = {
  trial: '试用',
  standard: '标准版',
  enterprise: '企业版',
  invalid: '许可无效',
}

function planColor(plan: string): string {
  if (plan === 'trial') return 'blue'
  if (plan === 'enterprise') return 'green'
  if (plan === 'standard') return 'gold'
  return 'red'
}

export default function LicenseBanner() {
  const license = useAppStore(s => s.license)
  const refreshLicense = useAppStore(s => s.refreshLicense)

  if (!license || license.mode === 'open') return null

  const plan = license.plan
  const label = PLAN_LABEL[plan] || plan
  const showStandardHint = plan === 'standard'
  const showTrialHint = plan === 'trial' && license.days_left != null && license.days_left <= 7
  const showInvalid = plan === 'invalid'

  if (!showStandardHint && !showTrialHint && !showInvalid) {
    return (
      <div className="dw-license-strip" style={{ padding: '4px 16px', background: 'var(--dw-header-bg, #fff)', borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
        <Space size="small">
          <Tag color={planColor(plan)}>{label}</Tag>
          {license.days_left != null && plan === 'trial' ? (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              剩余 {license.days_left} 天
            </Typography.Text>
          ) : null}
        </Space>
      </div>
    )
  }

  let message = ''
  let type: 'info' | 'warning' | 'error' = 'info'
  if (showInvalid) {
    type = 'error'
    message = '许可无效或已吊销，系统处于只读模式。请联系管理员在 Cloud GIDO 部署管理中续期。'
  } else if (showStandardHint) {
    type = 'warning'
    message = '试用已结束，当前为标准版（单工作空间、最多 5 用户，无高级角色/SSO/审计）。升级企业版可恢复全部能力。'
  } else {
    type = 'info'
    message = `试用还剩 ${license.days_left} 天，到期后将自动降为标准版。`
  }

  return (
    <Alert
      type={type}
      banner
      showIcon
      message={
        <Space wrap>
          <Tag color={planColor(plan)}>{label}</Tag>
          <span>{message}</span>
          <Button type="link" size="small" href="https://cloud-gido.com/zh/request-demo" target="_blank" rel="noreferrer">
            联系升级
          </Button>
          <Button type="link" size="small" onClick={() => void refreshLicense()}>
            刷新许可
          </Button>
        </Space>
      }
    />
  )
}

export function licenseAllowsMultiWorkspace(license: LicenseStatus | null): boolean {
  if (!license || license.mode === 'open') return true
  if (license.plan === 'invalid') return false
  return Boolean(license.features?.multi_workspace)
}

export function licenseAllowsAdvancedRbac(license: LicenseStatus | null): boolean {
  if (!license || license.mode === 'open') return true
  if (license.plan === 'invalid') return false
  return Boolean(license.features?.rbac_advanced)
}

export function licenseAllowsAudit(license: LicenseStatus | null): boolean {
  if (!license || license.mode === 'open') return true
  if (license.plan === 'invalid') return false
  return Boolean(license.features?.audit_log)
}

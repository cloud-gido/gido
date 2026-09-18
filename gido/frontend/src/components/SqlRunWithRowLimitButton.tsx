/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Databricks 式 Run ▾：主按钮运行，右侧展开试跑行上限（低频，不占顶栏）。
 */
import { Button, Dropdown, InputNumber, Space, Tooltip, Typography } from 'antd'
import { DownOutlined, PlayCircleOutlined } from '@ant-design/icons'
import {
  SQL_RESULT_ROW_CAP,
  SQL_RESULT_ROW_LIMIT_PRESETS,
  clampSqlResultRowLimit,
  formatSqlResultRowLimitShort,
  sqlRunWithRowLimitLabel,
} from '../utils/sqlResultRowLimit'

type Props = {
  limit: number
  onLimitChange: (next: number) => void
  onRun: () => void
  loading?: boolean
  disabled?: boolean
  size?: 'small' | 'middle' | 'large'
  title?: string
}

export default function SqlRunWithRowLimitButton({
  limit,
  onLimitChange,
  onRun,
  loading = false,
  disabled = false,
  size = 'small',
  title,
}: Props) {
  const clamped = clampSqlResultRowLimit(limit)

  const control = (
    <Dropdown.Button
      type="primary"
      size={size}
      disabled={disabled}
      loading={loading}
      icon={<DownOutlined />}
      trigger={['click']}
      onClick={() => onRun()}
      popupRender={() => (
        <div
          role="dialog"
          aria-label="试跑行上限"
          onClick={event => event.stopPropagation()}
          style={{
            background: '#fff',
            border: '1px solid #f0f0f0',
            borderRadius: 8,
            padding: 12,
            minWidth: 280,
            boxShadow: '0 6px 16px rgba(0,0,0,0.08)',
          }}
        >
          <Typography.Text strong style={{ display: 'block', marginBottom: 4 }}>
            试跑行上限
          </Typography.Text>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 10, fontSize: 12 }}>
            仅影响下次运行的物化上限（硬顶 {SQL_RESULT_ROW_CAP.toLocaleString()}）。网格为预览，完整数据请导出。
          </Typography.Paragraph>
          <Space wrap size={6} style={{ marginBottom: 10 }}>
            {SQL_RESULT_ROW_LIMIT_PRESETS.map(preset => (
              <Button
                key={preset}
                size="small"
                type={clamped === preset ? 'primary' : 'default'}
                onClick={() => onLimitChange(preset)}
              >
                {formatSqlResultRowLimitShort(preset)}
              </Button>
            ))}
          </Space>
          <Space size={8} align="center">
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>自定义</Typography.Text>
            <InputNumber
              size="small"
              min={1}
              max={SQL_RESULT_ROW_CAP}
              value={clamped}
              onChange={value => onLimitChange(clampSqlResultRowLimit(value, clamped))}
              style={{ width: 110 }}
              aria-label="自定义试跑行上限"
            />
          </Space>
        </div>
      )}
    >
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        {!loading ? <PlayCircleOutlined /> : null}
        {sqlRunWithRowLimitLabel(clamped, loading)}
      </span>
    </Dropdown.Button>
  )

  if (!title) return control
  return (
    <Tooltip title={title}>
      <span style={{ display: 'inline-flex' }}>{control}</span>
    </Tooltip>
  )
}

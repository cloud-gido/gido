/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * GIDO 统一时间展示：列表用紧凑月日+时分秒，悬停看完整年月日（空间时区）。
 * 实例中心 / 告警中心 / 运行历史等同口径，禁止直接甩 ISO 字符串。
 */
import { Tooltip } from 'antd'
import { formatInTimeZone, formatInTimeZoneCompact } from '../utils/datetime'

type Props = {
  value?: string | null
  timeZone: string
  /** list：紧凑+Tooltip；detail：完整一行 */
  variant?: 'list' | 'detail'
}

export default function WorkspaceTime({ value, timeZone, variant = 'list' }: Props) {
  if (variant === 'detail') {
    return (
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>
        {formatInTimeZone(value, timeZone)}
      </span>
    )
  }
  const full = formatInTimeZone(value, timeZone)
  if (!value || full === '—') return <span>—</span>
  return (
    <Tooltip title={full}>
      <span style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12.5 }}>
        {formatInTimeZoneCompact(value, timeZone)}
      </span>
    </Tooltip>
  )
}

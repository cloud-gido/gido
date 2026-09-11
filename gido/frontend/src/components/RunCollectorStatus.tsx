/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */
import { Alert, Tag, Tooltip } from 'antd'
import { CheckCircleFilled } from '@ant-design/icons'

/**
 * 运行数据采集状态：实例中心与告警中心共用。
 * 只讲「GIDO 是否在持续收生产运行数据」，不暴露底层执行引擎。
 *
 * 语义要分清，避免平台「看起来坏了」：
 * - 尚未首次成功：黄，等待中
 * - 曾经成功、现在超时未再成功：红，真的落后
 * - 未启用 / 状态未知：黄
 */
export type RunCollector = {
  enabled?: boolean | null
  interval_seconds?: number
  lag_seconds?: number | null
  last_success_at?: string | null
  stale?: boolean
  last_error?: string | null
}

function lagLabel(lag?: number | null): string {
  if (lag == null) return '尚未采集'
  if (lag < 60) return `${lag} 秒前`
  if (lag < 3600) return `${Math.floor(lag / 60)} 分钟前`
  return `${Math.floor(lag / 3600)} 小时前`
}

export default function RunCollectorStatus({ collector }: { collector?: RunCollector | null }) {
  if (!collector) return null

  if (collector.enabled === false) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="生产调度未启用，暂不会产生运行实例与失败告警"
        description="平台管理员可在「系统管理 → 平台集成」启用生产调度，或在空间设置中为本空间单独配置。"
      />
    )
  }

  // enabled 为 null：探测失败，不能谎报绿
  if (collector.enabled == null) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="无法确认运行数据采集状态"
        description={
          collector.last_error
            ? `最近一次采集报错：${collector.last_error}`
            : 'GIDO 没能判断生产调度是否启用，实例与告警可能不完整。请平台管理员检查「系统管理 → 平台集成」的生产调度配置。'
        }
      />
    )
  }

  const interval = collector.interval_seconds || 15

  // 一次都没成功：黄，不要用「已落后」——那是「断了」的语义，会让人以为平台坏了
  if (collector.lag_seconds == null) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="运行数据尚未完成首次采集"
        description={
          collector.last_error
            ? `最近一次尝试失败：${collector.last_error}。可点实例中心的「立即采集」重试，或检查生产调度连通性与凭证。`
            : `后台约每 ${interval} 秒自动采集一轮；也可在实例中心点「立即采集」。稍等一轮后应变为「实时采集中」。`
        }
      />
    )
  }

  // 真落后：曾经采到过，现在超时了
  if (collector.stale) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ marginBottom: 16 }}
        message={`运行数据采集已落后（最近一次成功：${lagLabel(collector.lag_seconds)}）`}
        description={
          collector.last_error
            ? `失败原因：${collector.last_error}`
            : '实例与告警可能不是最新。请联系平台管理员检查生产调度连通性与凭证，或在实例中心点「立即采集」。'
        }
      />
    )
  }

  return (
    <Tooltip title={`GIDO 每 ${interval} 秒采集一次生产运行数据，无需手动同步`}>
      <Tag icon={<CheckCircleFilled />} color="success" style={{ marginBottom: 12 }}>
        运行数据实时采集中 · 最近 {lagLabel(collector.lag_seconds)}
      </Tag>
    </Tooltip>
  )
}

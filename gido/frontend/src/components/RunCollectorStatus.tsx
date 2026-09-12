/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */
import { Alert } from 'antd'

/**
 * 运行数据采集：正常时不打扰用户（实例列表的「最近同步」已够用）。
 * 只在「调度未开 / 确认不了 / 真的落后」时出横幅，聚焦运维与告警本身。
 */
export type RunCollector = {
  enabled?: boolean | null
  interval_seconds?: number
  lag_seconds?: number | null
  last_success_at?: string | null
  last_duration_seconds?: number | null
  stale?: boolean
  in_progress?: boolean
  stuck?: boolean
  last_error?: string | null
}

function lagLabel(lag?: number | null): string {
  if (lag == null) return '尚未成功'
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
        message="生产调度未启用，暂无运行实例与失败告警"
        description="平台管理员可在「系统管理 → 平台集成」启用，或在空间设置中单独配置。"
      />
    )
  }

  if (collector.enabled == null) {
    return (
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="无法确认生产调度是否可用"
        description={
          collector.last_error
            ? `最近报错：${collector.last_error}`
            : '请平台管理员检查「系统管理 → 平台集成」中的生产调度配置。'
        }
      />
    )
  }

  // 健康 / 正在采 / 尚未首采：不展示。用户看实例更新时间即可。
  if (!collector.stale) return null

  return (
    <Alert
      type="error"
      showIcon
      style={{ marginBottom: 16 }}
      message={`运行数据可能不是最新（最近成功：${lagLabel(collector.lag_seconds)}）`}
      description={
        collector.last_error
          ? `原因：${collector.last_error}。可在实例中心点「立即采集」重试。`
          : '若刚发布大量工作流可稍等；持续如此请检查生产调度连通性。也可点「立即采集」。'
      }
    />
  )
}

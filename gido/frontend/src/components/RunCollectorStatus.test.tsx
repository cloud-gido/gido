/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import RunCollectorStatus from './RunCollectorStatus'

// 这个仓库没开自动 cleanup，不清理的话上一条用例的 DOM 会留在文档里，
// 让「不应该出现」的断言查到前一次渲染的内容。
afterEach(cleanup)

describe('RunCollectorStatus', () => {
  it('采集正常时只挂一个绿色标签', () => {
    render(<RunCollectorStatus collector={{ enabled: true, lag_seconds: 8, stale: false }} />)
    expect(screen.getByText(/运行数据实时采集中/)).toBeTruthy()
  })

  it('落后时报错并带上失败原因', () => {
    render(<RunCollectorStatus collector={{ enabled: true, stale: true, lag_seconds: 600, last_error: '连接被拒绝' }} />)
    expect(screen.getByText(/采集已落后/)).toBeTruthy()
    expect(screen.getByText(/连接被拒绝/)).toBeTruthy()
  })

  it('生产调度未启用时说清楚不会有实例和告警', () => {
    render(<RunCollectorStatus collector={{ enabled: false }} />)
    expect(screen.getByText(/生产调度未启用/)).toBeTruthy()
  })

  it('enabled 未知时不能谎报正常', () => {
    // 后端探测不出引擎状态时 enabled=null，此时 stale 恒为 false，
    // 旧实现会一路走到绿色「实时采集中」——正好在最该报警时说一切正常。
    render(<RunCollectorStatus collector={{ enabled: null, stale: false, lag_seconds: null }} />)
    expect(screen.queryByText(/运行数据实时采集中/)).toBeNull()
    expect(screen.getByText(/无法确认运行数据采集状态/)).toBeTruthy()
  })

  it('一次都没采集成功时不说「实时采集中」', () => {
    render(<RunCollectorStatus collector={{ enabled: true, stale: false, lag_seconds: null }} />)
    expect(screen.queryByText(/实时采集中/)).toBeNull()
    expect(screen.getByText(/尚未完成首次采集/)).toBeTruthy()
  })

  it('后端没给采集信息时不渲染任何东西（兼容旧后端）', () => {
    const { container } = render(<RunCollectorStatus collector={null} />)
    expect(container.textContent).toBe('')
  })
})

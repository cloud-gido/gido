/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import RunCollectorStatus from './RunCollectorStatus'

afterEach(() => {
  cleanup()
})

describe('RunCollectorStatus', () => {
  it('healthy collector stays silent', () => {
    const { container } = render(
      <RunCollectorStatus collector={{ enabled: true, lag_seconds: 8, stale: false }} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('in-progress collection stays silent', () => {
    const { container } = render(
      <RunCollectorStatus
        collector={{ enabled: true, in_progress: true, stale: false, lag_seconds: 180 }}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('never-synced yet stays silent', () => {
    const { container } = render(
      <RunCollectorStatus collector={{ enabled: true, stale: false, lag_seconds: null }} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows only when truly stale', () => {
    render(
      <RunCollectorStatus
        collector={{ enabled: true, stale: true, lag_seconds: 600, last_error: '连接被拒绝' }}
      />,
    )
    expect(screen.getByText(/运行数据可能不是最新/)).toBeTruthy()
    expect(screen.getByText(/连接被拒绝/)).toBeTruthy()
  })

  it('warns when production scheduler disabled', () => {
    render(<RunCollectorStatus collector={{ enabled: false }} />)
    expect(screen.getByText(/生产调度未启用/)).toBeTruthy()
  })

  it('warns when enabled status unknown', () => {
    render(
      <RunCollectorStatus collector={{ enabled: null, stale: false, last_error: '探测失败' }} />,
    )
    expect(screen.getByText(/无法确认生产调度是否可用/)).toBeTruthy()
  })

  it('renders nothing without collector payload', () => {
    const { container } = render(<RunCollectorStatus collector={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */
import { describe, expect, it } from 'vitest'
import { describePollError } from './pollError'

describe('describePollError', () => {
  it('把 524 说成服务端超时，而不是让用户去猜', () => {
    const msg = describePollError({ response: { status: 524 } })
    expect(msg).toContain('超时')
    expect(msg).toContain('自动重试')
  })

  it('504 与 408 同样归为超时', () => {
    for (const status of [504, 408]) {
      expect(describePollError({ response: { status } })).toContain('超时')
    }
  })

  it('502 / 503 说成服务暂时不可用', () => {
    for (const status of [502, 503]) {
      expect(describePollError({ response: { status } })).toContain('暂时不可用')
    }
  })

  it('后端给了 detail 就直接用后端的话', () => {
    const msg = describePollError({ response: { status: 400, data: { detail: '业务日期格式须为 YYYY-MM-DD' } } })
    expect(msg).toBe('业务日期格式须为 YYYY-MM-DD')
  })

  it('403 用后端的 detail，没有则给权限提示', () => {
    expect(describePollError({ response: { status: 403, data: { detail: '仅平台管理员可查看全部工作空间' } } }))
      .toBe('仅平台管理员可查看全部工作空间')
    expect(describePollError({ response: { status: 403 } })).toContain('权限')
  })

  it('压根没有响应时按网络失败处理，不要显示 undefined', () => {
    const msg = describePollError({ message: 'Network Error' })
    expect(msg).toContain('网络')
    expect(msg).not.toContain('undefined')
  })

  it('任何情况下都不返回空串，横幅不能出现空白', () => {
    for (const e of [{}, null, undefined, { response: {} }, { response: { status: 418 } }]) {
      expect(describePollError(e).length).toBeGreaterThan(0)
    }
  })
})

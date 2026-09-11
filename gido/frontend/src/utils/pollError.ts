/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-09-11
 */

/**
 * 把轮询请求的失败翻译成一句人话。
 *
 * 实例中心和告警中心都每 15 秒轮一次，失败时不能弹 toast（会把屏幕刷满），
 * 也不能什么都不做（控制台一堆 Uncaught AxiosError，页面只剩转圈）。
 * 统一挂一条横幅，并且明确告诉用户「会自动重试」，免得以为要手动刷新。
 */
export function describePollError(e: any): string {
  const status = Number(e?.response?.status || 0)
  const detail = e?.response?.data?.detail

  // 524/504/408 是网关等源站超时：后端还在跑或者卡住了，不是权限或参数问题
  if (status === 524 || status === 504 || status === 408) {
    return '服务端响应超时，正在自动重试。若持续出现，请检查后端与调度采集是否正常。'
  }
  if (status === 502 || status === 503) {
    return '后端服务暂时不可用，正在自动重试。'
  }
  if (status === 403) {
    return typeof detail === 'string' && detail ? detail : '没有查看权限。'
  }
  if (typeof detail === 'string' && detail) {
    return detail
  }
  if (status >= 500) {
    return `服务端出错（${status}），正在自动重试。`
  }
  // 压根没拿到响应：断网、被拦截、或者请求被浏览器取消
  if (!e?.response) {
    return '网络请求失败，正在自动重试。'
  }
  return `加载失败（${status}），正在自动重试。`
}

/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
import axios from 'axios'

/**
 * 默认 `/api`：由 Vite dev 代理或 Nginx 转到后端。
 * 若静态页不经代理直连后端，在 frontend/.env 设置：
 *   VITE_API_ORIGIN=http://127.0.0.1:8001
 * 则请求发往该地址下的 /api。
 */
const apiOrigin = (import.meta.env.VITE_API_ORIGIN as string | undefined)?.replace(/\/$/, '') ?? ''
const baseURL = apiOrigin ? `${apiOrigin}/api` : '/api'

/** Flink 提交可轮询到 ~180s，需长于 nginx/浏览器侧默认 */
const request = axios.create({ baseURL, timeout: 330000 })

request.interceptors.request.use(config => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

request.interceptors.response.use(
  res => res.data,
  err => {
    // Nginx 502/503 常返回整页 HTML，避免把 <html>… 原样塞进 message 弹窗
    const st = err.response?.status
    const data = err.response?.data
    if (st && typeof data === 'string') {
      const raw = data.trim()
      if (raw.startsWith('<') || /<title>\s*50[234]|Bad Gateway|Service Temporarily Unavailable/i.test(data)) {
        const hint: Record<number, string> = {
          502: '网关无响应（502）：后端未启动或已崩溃，请检查 gido 后端（docker logs gido-backend）。',
          503: '服务暂时不可用（503），请稍后重试或检查后端状态。',
          504: '网关超时（504），请稍后重试。',
        }
        err.response!.data = { detail: hint[st] || `HTTP ${st}：网关返回了错误页` }
      }
    }
    // 登录失败也会返回 401，不应整页跳转（否则打断错误提示且易与开发热更新冲突）
    const path = String(err.config?.url ?? '')
    const base = String(err.config?.baseURL ?? '')
    const isLoginAttempt = path.includes('auth/login') || base.includes('auth/login')
    if (err.response?.status === 401 && !isLoginAttempt) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default request

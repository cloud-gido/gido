/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/** 经 GIDO 代理打开 Operator Flink Web UI（自动 bootstrap Cookie，无需 kubectl port-forward JM）。 */
export function openFlinkConsoleUrl(consoleUrl: string, jobId: number): void {
  const token = localStorage.getItem('token')
  if (!token) {
    window.location.href = '/login'
    return
  }
  const prefix = `/api/streaming/jobs/${jobId}/flink-ui`
  if (!consoleUrl.startsWith(prefix)) {
    window.open(consoleUrl, '_blank', 'noopener,noreferrer')
    return
  }
  // 无 hash 时先进总览，避免 DB 中过期 jobId 导致 Flink 空白页
  let target = consoleUrl
  if (!target.includes('#')) {
    target = `${prefix}/#/overview`
  }
  const then = encodeURIComponent(target)
  const bootstrap = `/api/streaming/jobs/${jobId}/flink-ui/bootstrap?access_token=${encodeURIComponent(token)}&then=${then}`
  window.open(bootstrap, '_blank', 'noopener,noreferrer')
}

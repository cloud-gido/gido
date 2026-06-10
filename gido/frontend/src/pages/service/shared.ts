/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export function formatApiError(e: any, fallback = '操作失败'): string {
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((item: any) => item?.msg || JSON.stringify(item)).join('; ')
  if (detail && typeof detail === 'object') return detail.message || JSON.stringify(detail)
  return e?.message || fallback
}

export const STATUS_COLOR: Record<string, string> = {
  draft: 'default',
  online: 'success',
  offline: 'warning',
}

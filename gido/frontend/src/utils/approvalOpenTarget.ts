/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * 发布审批资源 → 完整配置页深链（辅入口；主入口为审批 Drawer 预览）。
 */
import { R } from '../routes'

export type ApprovalResourceRow = {
  resource_type: string
  resource_id: number
  resource_name?: string
}

export function approvalResourceOpenPath(row: ApprovalResourceRow): string | null {
  const id = row.resource_id
  if (!id) return null
  switch (row.resource_type) {
    case 'studio_node':
      return `${R.batch.studio}?node_id=${id}`
    case 'workflow':
      return `${R.batch.workflow}?workflow_id=${id}`
    case 'stream_job':
      return `${R.stream.studio}?job_id=${id}`
    case 'data_service_api':
      return `${R.service.apis}?api_id=${id}`
    default:
      return null
  }
}

export function approvalResourceOpenLabel(resourceType: string): string {
  switch (resourceType) {
    case 'studio_node':
      return '在数据开发中打开'
    case 'workflow':
      return '在工作流中打开'
    case 'stream_job':
      return '在实时开发中打开'
    case 'data_service_api':
      return '在数据服务 API 中打开'
    default:
      return '打开完整配置'
  }
}

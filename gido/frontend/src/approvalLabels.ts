/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 */
/** 发布审批文案（GIDO Batch / Stream / Serve 共用） */

export const APPROVAL_RESOURCE_LABEL: Record<string, string> = {
  workflow: '工作流',
  studio_node: '开发脚本',
  stream_job: '实时作业',
  data_service_api: '数据服务 API',
}

export const APPROVAL_ACTION_LABEL: Record<string, string> = {
  publish_to_ds: '发布到 Dolphin（生产）',
  publish_node: '提交脚本（生产）',
  submit_job: '提交到 Flink（生产）',
  publish_api: 'API 发布上线',
  offline_api: 'API 下线',
}

export function approvalPendingKey(resourceType: string, resourceId: number, action: string) {
  return `${resourceType}:${resourceId}:${action}`
}

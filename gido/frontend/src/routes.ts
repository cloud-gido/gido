/**
 * Copyright 2026 玑渡 GIDO Contributors
 * SPDX-License-Identifier: Apache-2.0
 * @author felixzhu
 * @date 2026-06-05
 */
export const R = {
  login: '/login',
  about: '/about',
  batch: {
    root: '/gido/batch',
    studio: '/gido/batch/studio',
    workflow: '/gido/batch/workflow',
    datamap: '/gido/batch/datamap',
    probe: '/gido/batch/probe',
    quality: '/gido/batch/quality',
    integration: '/gido/batch/integration',
    operation: '/gido/batch/operation',
    approval: '/gido/batch/approval',
    datasource: '/gido/batch/datasource',
    /** @deprecated 旧路径，仅用于菜单策略判断 */
    legacyService: '/gido/batch/dataservice',
    workspaceSettings: '/gido/batch/workspace-settings',
    admin: '/gido/batch/admin',
    systemIntegration: '/gido/batch/system/integration',
  },
  stream: {
    root: '/gido/stream',
    studio: '/gido/stream/studio',
    monitor: '/gido/stream/monitor',
    overview: '/gido/stream/overview',
    /** @deprecated Session 模式已移除；保留路径供旧链接重定向 */
    flinkSessions: '/gido/stream/flink-sessions',
    approval: '/gido/stream/approval',
  },
  service: {
    root: '/gido/service',
    overview: '/gido/service/overview',
    apis: '/gido/service/apis',
    apps: '/gido/service/apps',
    monitor: '/gido/service/monitor',
    gateway: '/gido/service/gateway',
    datasource: '/gido/service/datasource',
    approval: '/gido/service/approval',
  },
} as const

export type ProductId = 'batch' | 'stream' | 'service'
